"""Step 2 of the notebook: the preview, the confirmation and the image type."""

from __future__ import annotations

import html

from . import notebook_style as ui
from .axes import AxisMapping, axis_name, file_mapping_for
from .axis_review import review_axes
from .image_type import review_image_type
from .notebook_options import IMAGE_TYPE_NAMES
from .notebook_plots import plane_png
from .notebook_render import preview_caption
from .planes import PlaneReadError, read_plane
from .validation import plural


class _AxisPreview:
    """Step 2's preview, the confirmation of the axis roles and the image type."""

    def _fill_preview_files(self) -> None:
        """List the images that fit the roles in the preview dropdown."""
        files = [
            f
            for f in self.preflight.metadata.files
            if f.readable and file_mapping_for(f, self.mapping) is not None
        ]
        options = [
            (
                f"{f.relative_path}  ({'OME-Zarr, ' if f.is_ome_zarr else ''}{f.axes}, "
                f"{' × '.join(map(str, f.shape))})",
                f.relative_path,
            )
            for f in files
        ]
        self.preview_file.options = options
        self.preview_file.value = options[0][1] if options else None

    def _preview_file_changed(self) -> None:
        if self._preview_shown:
            self._configure_sliders(reset=True)
            self._render_preview()

    def _configure_sliders(self, reset: bool) -> None:
        header = self._preview_header()
        file_mapping = file_mapping_for(header, self.mapping) if header else None
        for role, slider in self.preview_sliders.items():
            dim = None if file_mapping is None else file_mapping.index_of(role)
            length = 1 if dim is None else header.shape[dim]
            middle = length // 2 if role == "Z" else 0
            with slider.hold_trait_notifications():
                slider.max = max(length - 1, 0)
                slider.value = middle if reset else min(slider.value, slider.max)
            ui.set_visible(slider, length > 1)

    def show_preview(self) -> None:
        """Show a plane of the chosen image with the current roles; sliders pick others."""
        if self.preflight is None or self.mapping is None:
            return
        if not self._preview_shown:
            self._configure_sliders(reset=True)
            self._preview_shown = True
        self._render_preview()
        self.preview_checked.disabled = False

    def _render_preview_if_shown(self) -> None:
        if self._preview_shown:
            self._render_preview()

    def _render_preview(self) -> None:
        """Draw the chosen plane with the roles shown, labelled confirmed or not (PP-10)."""
        header = self._preview_header()
        if header is None:
            self.preview_caption.value = ui.status("warn", "No image fits these axis roles.")
            return
        file_mapping = file_mapping_for(header, self.mapping)
        coords = {
            role.lower(): self.preview_sliders[role].value
            for role in ("Z", "C", "T")
            if file_mapping.index_of(role) is not None
        }
        try:
            with self.log.attached():
                plane = read_plane(
                    self.preflight.discovery.root / header.relative_path,
                    file_mapping,
                    memory_limit=self._memory_limit(),
                    preview=True,
                    **coords,
                )
        except (PlaneReadError, IndexError, ValueError) as exc:
            self.preview_image.value = b""
            ui.set_visible(self.preview_image, False)
            self.preview_caption.value = ui.status(
                "warn", "This plane cannot be shown with these roles.", [str(exc)]
            )
            return
        self.preview_image.value = plane_png(plane)
        ui.set_visible(self.preview_image, True)
        self.preview_caption.value = self._preview_label() + preview_caption(
            header.relative_path, plane
        )
        self._show_log()

    def _preview_label(self) -> str:
        """Say whether the preview shows the confirmed roles or roles still being edited."""
        if self.confirmed_mapping is not None:
            return ui.status("ok", "Preview with the confirmed axis roles.")
        return ui.status(
            "info",
            "Preview of the roles above, not confirmed yet.",
            [
                "Nothing is written: the run uses only the roles you confirm below.",
            ],
        )

    def _preview_header(self):  # noqa: ANN202
        value = self.preview_file.value
        return next((f for f in self.preflight.metadata.files if f.relative_path == value), None)

    def _toggle_confirmation(self) -> None:
        """Confirm the roles shown (or take the confirmation back) and update what follows."""
        if self.confirmed_mapping is None:
            if self.confirm_button.disabled:
                return
            self.confirmed_mapping = self._current_mapping()
        else:
            self.confirmed_mapping = None
        locked = self.confirmed_mapping is not None
        self._set_locked(locked)
        if locked:
            roles = [
                f"{axis_name(a)} → {r}"
                for a, r in zip(
                    self.confirmed_mapping.axes, self.confirmed_mapping.roles, strict=True
                )
                if r is not None
            ]
            review = review_axes(
                self.preflight.metadata,
                self.confirmed_mapping,
                missing_as_singleton=self.missing_as_singleton.value,
            )
            confirmed = review.confirmed_files
            lines = [
                "The confirmed roles agree with every axis the image metadata names."
                if confirmed
                else "Every image names its axes in its metadata, and the confirmed roles "
                "agree with them.",
                *(
                    [
                        f"{plural(len(confirmed), 'image')} do not name their axes: their "
                        "roles are recorded as confirmed by you."
                    ]
                    if confirmed
                    else []
                ),
                *roles,
            ]
            self.confirmed_status.value = ui.status(
                "ok", "Axes checked: they are correct for every image.", lines
            )
            self._open_image_type()
            self._refresh_axis_next()
        else:
            self.confirmed_status.value = ""
            # The image type depends on the channels the roles give.
            self._close_image_type()
            self._mapping_changed()
        self._update_confirm_button()
        self._render_preview_if_shown()
        self._show_calibration()
        self._refresh_run_step()
        self._refresh_channels()

    def _open_image_type(self) -> None:
        """Suggest the image type for the confirmed roles and ask the user to confirm it."""
        review = review_axes(
            self.preflight.metadata,
            self.confirmed_mapping,
            missing_as_singleton=self.missing_as_singleton.value,
        )
        self._image_type_review = review_image_type(self.preflight.metadata, review)
        self.image_type_checked.set(False, notify=False)
        self.confirmed_image_type = None
        # The suggestion is only preselected; nothing is used until it is confirmed.
        # The earlier run's type comes first (audit I4), as the run would keep it.
        self.image_type.value = (
            self._recorded_image_type(self._image_type_review.rgb_allowed)
            or self._image_type_review.suggested
        )
        self.image_type.disabled = not self._image_type_review.rgb_allowed
        self.image_type_checked.disabled = False
        self._show_image_type_note()
        ui.set_visible(self.image_type_card, True)

    def _close_image_type(self) -> None:
        self._image_type_review = None
        self.confirmed_image_type = None
        self.image_type_checked.set(False, notify=False)
        self.image_type_note.value = ""
        ui.set_visible(self.image_type_card, False)

    def _show_image_type_note(self) -> None:
        """Explain the suggested image type, and warn when another is chosen."""
        review = self._image_type_review
        if review is None:
            return
        lines = [
            f"Suggested: <b>{IMAGE_TYPE_NAMES[review.suggested]}</b>, because "
            f"{html.escape(review.reason)}."
        ]
        if not review.rgb_allowed:
            lines.append(f"RGB cannot be chosen: {html.escape(review.rgb_problem)}")
        kind = "info"
        if self.image_type.value != review.suggested:
            kind = "warn"
            lines.append(
                f"You chose {IMAGE_TYPE_NAMES[self.image_type.value]}, not the suggested type. "
                "Confirm only if you are sure."
            )
        self.image_type_note.value = ui.status(
            kind, f"Image type: {IMAGE_TYPE_NAMES[self.image_type.value]}", lines, raw=True
        )

    def _image_type_answered(self, confirmed: bool) -> None:
        self.confirmed_image_type = self.image_type.value if confirmed else None
        review = self._image_type_review
        self.image_type.disabled = confirmed or review is None or not review.rgb_allowed
        if confirmed:
            self.image_type_checked.confirmed_text = (
                f"Image type confirmed: {IMAGE_TYPE_NAMES[self.confirmed_image_type]}."
            )
        if self.confirmed_mapping is not None:
            self._refresh_axis_next()
        self._refresh_run_step()

    def _refresh_axis_next(self) -> None:
        """Say what to do next once the axis roles are confirmed."""
        if self.confirmed_image_type is None:
            self.axis_next.value = ui.next_steps(
                "Confirm the image type in Step 2.",
                "Check the suggested type under Image type, then click Yes, this is the right "
                "image type.",
            )
        elif not self.labels_confirmed.value:
            self.axis_next.value = ui.next_steps(
                "Go to Step 1 and confirm the folder labels.",
                "Review the labels and click Yes, the labels are correct before running Step 3.",
            )
        elif self._design_pending():
            self.axis_next.value = ui.next_steps(
                "Go to Step 1 and confirm the biological repeats.",
                "Under Experiment design, answer each yellow question before running Step 3.",
            )
        else:
            self.axis_next.value = ui.next_steps(
                "Go to Step 3 and compute the histograms.",
                "Run its cell and review the settings and What will happen. "
                "Then click Compute histograms.",
            )

    def _set_locked(self, locked: bool) -> None:
        for dropdown in self.role_dropdowns:
            dropdown.disabled = locked
        self.missing_as_singleton.disabled = locked
        self.preview_checked.disabled = locked or not self._preview_shown
        self.confirm_button.description = " Change axis roles" if locked else " Confirm axis roles"
        self.confirm_button.icon = "pencil" if locked else "check"
        self.confirm_button.button_style = "warning" if locked else "success"

    def _current_mapping(self) -> AxisMapping:
        return AxisMapping(self.mapping.axes, tuple(d.value for d in self.role_dropdowns))
