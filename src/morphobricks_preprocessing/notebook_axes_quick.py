"""*Confirm these axes*: the axes step in one click, when nothing about the axes is open.

Owner decision Q3 (UX proposal 2026-11-09): when the suggested roles are complete and nothing
in them is uncertain, the axes step offers one **Confirm these axes** button under the role
boxes and the example (redesign of 10 November 2026: the boxes and the example are always
shown; the button replaces the tick and *Confirm axis roles* while it is offered). On a reopened
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
from .description_reconcile import CHANNELS_CHANGED
from .fingerprint import unchanged_confirmation
from .notebook_render import blocked_line


class _QuickAxes:
    """The one-click confirmation card of the stepper's axes step."""

    def _build_quick_axes(self) -> None:
        """Build the one-click confirmation (hidden until roles can be offered)."""
        self._quick_offer: AxisMapping | None = None
        self._quick_filling = False
        # The check whose recorded roles were offered: once offered, a role the user changes is
        # theirs, and the recorded roles are not put back into the boxes.
        self._quick_seeded: object | None = None
        self.quick_text = widgets.HTML()
        self.quick_caption = widgets.HTML()
        self.quick_confirm = ui.button(
            " Confirm these axes",
            style="success",
            icon="check",
            tooltip="The example looks right: use these roles for every image",
            main=True,
        )
        self.quick_confirm.on_click(lambda _: self._confirm_quick_axes())
        self.quick_card = widgets.VBox([self.quick_text, self.quick_confirm, self.quick_caption])
        ui.set_visible(self.quick_card, False)

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

    def _refresh_quick_axes(self, drawn: bool = False) -> None:
        """Offer the roles for one click, or the tick and *Confirm axis roles*.

        The example is read again here (the image as the folder check read it), unless
        ``drawn`` says the caller has just drawn it.
        """
        if getattr(self, "quick_card", None) is None or self._quick_filling:
            return
        offer = None if self.confirmed_mapping is not None else self._quick_mapping()
        self._quick_offer = offer
        if offer is not None and self._saved_axes() is not None:
            self._quick_seeded = self.preflight  # the recorded roles go into the boxes once
        offered = offer is not None
        ui.set_visible(self.quick_card, offered)
        # One click when nothing is open; else the tick and Confirm axis roles (also to change).
        for widget in (self.preview_checked.widget, self.preview_blocker, self.confirm_button,
                       self.confirm_blocker):  # fmt: skip
            ui.set_visible(widget, not offered)
        # The offer says the roles are the headers' and nothing is open: the line is not repeated;
        # once confirmed, the confirmation says it, and Change axis roles takes it back.
        confirmed = self.confirmed_mapping is not None
        ui.set_visible(self.axis_status, not offered and not confirmed)
        if confirmed:
            ui.set_visible(self.preview_checked.widget, False)
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
        if not drawn:
            self.show_preview()
        shown = self._quick_preview_shown(offer)
        self.quick_confirm.disabled = not shown
        self.quick_caption.value = blocked_line(
            [] if shown else ["no example of these roles is drawn: see the message under it"]
        )

    def _quick_preview_shown(self, offer: AxisMapping) -> bool:
        """Return whether the preview now shows a plane drawn with exactly the offered roles."""
        return self._preview_of(offer)

    def _quick_text(self, offer: AxisMapping) -> str:
        """Return what the card says: the roles, where they come from, what the click does."""
        roles = ", ".join(
            f"{html.escape(axis_name(axis))} → {role}"
            for axis, role in zip(offer.axes, offer.roles, strict=True)
            if role is not None
        )
        chosen = getattr(self, "_scan_roles", {})
        source = (
            "the roles the earlier run of this experiment recorded"
            if self._saved_axes() is not None
            else "the roles you chose on the example image (Images), which every header agrees with"
            if offer.axes in chosen and chosen[offer.axes].roles == offer.roles
            else "the roles every image header names"
        )
        return ui.status(
            "info",
            f"Suggested: {roles}.",
            [
                *self._headers_changed_line(),
                f"These are {source}. If the example shows your sample as you expect, confirm. "
                "Nothing is confirmed until you click. They can be changed above.",
            ],
            raw=True,
        )

    def _headers_changed_line(self) -> list[str]:
        """Say that the headers changed since the save, so the earlier roles are not offered."""
        state = self._published() if self.preflight is not None else None
        units = () if state is None else state.reconciliation.units
        if not any(CHANNELS_CHANGED in unit.reasons for unit in units):
            return []
        return [
            "Some image headers changed since the experiment was saved: the roles the earlier "
            "run recorded are not offered. Review these afresh."
        ]

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
        if not self._quick_preview_shown(offer):
            return False  # no preview of these roles was shown: the click cannot vouch for it
        self.preview_checked.set(True)  # the user's click says the preview looks right
        self._toggle_confirmation()
        return self.confirmed_mapping is not None


__all__ = ["_QuickAxes"]
