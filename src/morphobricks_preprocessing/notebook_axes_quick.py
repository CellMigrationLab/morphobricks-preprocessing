"""*Confirm these axes*: the axes step in one click, when nothing about the axes is open.

Owner decision Q3 (UX proposal 2026-11-09): when the suggested roles are complete and nothing
in them is uncertain, the axes step shows them with a preview and one **Confirm these axes**
button; the role boxes are folded under *Look closer, or change the roles*. On a reopened
experiment whose folders are unchanged, the roles the earlier run recorded are the ones offered,
so a reopening no longer stops on the axes.

Nothing is confirmed without the click. The click records exactly what the two clicks of the
role boxes record (the preview checked, then the roles confirmed), through the same code. The
roles are offered only when the axis review finds no message at all, every readable image fits
them with no missing axis to state, and every image names its axes in its header (a plain
stack only when the earlier run's confirmation still covers it unchanged); otherwise the role
boxes are shown open, as before.
"""

from __future__ import annotations

import html
from pathlib import Path

import ipywidgets as widgets

from . import notebook_style as ui
from .axes import AxisMapping, axis_name
from .axis_check import ConfirmedAxes
from .axis_review import review_axes
from .fingerprint import unchanged_confirmation

DETAILS_OPEN, DETAILS_CLOSED = " Hide the role boxes", " Look closer, or change the roles"


class _QuickAxes:
    """The one-click confirmation card of the stepper's axes step."""

    def _build_quick_axes(self) -> None:
        """Build the card (hidden until roles can be offered) and the fold of the role boxes."""
        self._quick_offer: AxisMapping | None = None
        self._quick_filling = False
        # The check whose recorded roles were offered: once offered, a role the user changes is
        # theirs, and the recorded roles are not put back into the boxes.
        self._quick_seeded: object | None = None
        self.quick_text = widgets.HTML()
        self.quick_image = widgets.Image(format="png", layout=widgets.Layout(max_width="420px"))
        ui.set_visible(self.quick_image, False)
        self.quick_caption = widgets.HTML()
        self.quick_confirm = ui.button(
            " Confirm these axes",
            style="success",
            icon="check",
            tooltip="The preview looks right: use these roles for every image",
            main=True,
        )
        self.quick_confirm.on_click(lambda _: self._confirm_quick_axes())
        self.quick_card = ui.card(
            "Suggested axes",
            [
                self.quick_text,
                widgets.HBox([self.quick_image]),
                self.quick_caption,
                self.quick_confirm,
            ],
            visible=False,
        )
        self.axes_details = ui.button(DETAILS_CLOSED, style="info", icon="sliders")
        self.axes_details.on_click(lambda _: self._toggle_axes_details())
        ui.set_visible(self.axes_details, False)

    def _toggle_axes_details(self) -> None:
        shown = not ui.is_visible(self.axis_box)
        ui.set_visible(self.axis_box, shown)
        self.axes_details.description = DETAILS_OPEN if shown else DETAILS_CLOSED

    # What can be offered -------------------------------------------------------------------

    def _saved_axes(self) -> AxisMapping | ConfirmedAxes | None:
        """Return the roles the earlier run recorded, on a reopening with unchanged folders."""
        state = self._published() if self.preflight is not None else None
        recorded = getattr(self, "_recorded", None)
        if state is None or state.changed or recorded is None or recorded.mapping is None:
            return None
        root = Path(self.preflight.discovery.root)
        saved = unchanged_confirmation(recorded.mapping, root)
        roles = saved.mapping if isinstance(saved, ConfirmedAxes) else saved
        if self.mapping is None or roles.axes != self.mapping.axes:
            return None
        edited = roles.roles != self.mapping.roles and self._quick_seeded is self.preflight
        return None if edited else saved

    def _quick_mapping(self) -> AxisMapping | None:
        """Return the roles to offer for one click, or None when the role boxes are needed."""
        pre = self.preflight
        if pre is None or self.mapping is None or not self.role_dropdowns:
            return None
        saved = self._saved_axes()
        covered: set[str] = set()
        if isinstance(saved, ConfirmedAxes):
            covered, saved = set(saved.files), saved.mapping
        mapping = saved or self.mapping
        review = review_axes(pre.metadata, mapping, missing_as_singleton=False)
        readable = [f for f in pre.metadata.files if f.readable]
        if (
            review.issues
            or len(review.file_mappings) != len(readable)
            or any(m.singleton_roles for m in review.file_mappings.values())
            or not set(review.confirmed_files) <= covered
        ):
            return None
        return mapping

    # Showing -----------------------------------------------------------------------------

    def _refresh_quick_axes(self) -> None:
        """Offer the roles for one click, or show the role boxes open."""
        if getattr(self, "quick_card", None) is None or self._quick_filling:
            return
        offer = None if self.confirmed_mapping is not None else self._quick_mapping()
        self._quick_offer = offer
        if offer is not None and self._saved_axes() is not None:
            self._quick_seeded = self.preflight  # the recorded roles go into the boxes once
        ui.set_visible(self.quick_card, offer is not None)
        ui.set_visible(self.axes_details, offer is not None)
        ui.set_visible(self.axis_box, offer is None)
        self.axes_details.description = DETAILS_CLOSED
        if offer is None:
            return
        if offer.roles != self.mapping.roles:
            self._quick_filling = True  # the recorded roles fill the boxes, as other inputs
            try:
                for box, role in zip(self.role_dropdowns, offer.roles, strict=True):
                    box.value = role or "-"
            finally:
                self._quick_filling = False
        self.quick_text.value = self._quick_text(offer)
        self.show_preview()
        self.quick_image.value = self.preview_image.value
        ui.set_visible(self.quick_image, bool(self.preview_image.value))
        self.quick_caption.value = self.preview_caption.value

    def _quick_text(self, offer: AxisMapping) -> str:
        """Return what the card says: the roles, where they come from, what the click does."""
        roles = ", ".join(
            f"{html.escape(axis_name(axis))} → {role}"
            for axis, role in zip(offer.axes, offer.roles, strict=True)
            if role is not None
        )
        source = (
            "the roles the earlier run of this experiment recorded"
            if self._saved_axes() is not None
            else "the roles every image header names"
        )
        return ui.status(
            "info",
            f"Suggested: {roles}.",
            [
                f"These are {source}; the axis check finds nothing to ask about them.",
                "Look at the preview: if it looks like a normal image of your sample, confirm. "
                "Nothing is confirmed until you click.",
            ],
            raw=True,
        )

    # The click ---------------------------------------------------------------------------

    def _confirm_quick_axes(self) -> bool:
        """Confirm the offered roles: the preview checked, then the roles, as the role boxes do.

        Returns
        -------
        bool
            True when the roles are now confirmed.
        """
        offer = self._quick_offer
        if offer is None or self.confirmed_mapping is not None:
            return False
        if self._current_mapping() != offer:
            self._refresh_quick_axes()
            return False
        self.preview_checked.set(True)  # the user's click says the preview looks right
        self._toggle_confirmation()
        return self.confirmed_mapping is not None


__all__ = ["DETAILS_CLOSED", "DETAILS_OPEN", "_QuickAxes"]
