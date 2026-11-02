"""Step 1 of the notebook: the calibration card (design v0.4 §3, PP-10).

The card shows the pixel sizes each image is recorded with, and their source: the image
metadata (``header``), a size stated in ``calibration.csv`` (``stated``), ``pixels``
(deliberately uncalibrated), or none (uncalibrated). The user states sizes per target (the
whole experiment, a folder or an image), axis and unit through the same API as the
``calibrate`` command (:func:`.calibration.calibration_entry`,
:func:`.run.calibrate.update_calibration`, under the output folder's lock): no second rule
set. Before the axis roles are confirmed the sizes are those the folder check found; once
they are confirmed, a Z or T size of an image without that axis is not shown, as the run
records none (:func:`.run.preflight.fit_calibration`).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import ipywidgets as widgets

from . import notebook_style as ui
from .axis_review import review_axes
from .calibration import CALIBRATION_FILENAME, Calibration, calibration_entry
from .calibration_io import CalibrationFormatError
from .notebook_options import _WIDE
from .notebook_render import issues_html
from .notebook_values import size_rows
from .recorded_sizes import PIXELS, SIZE_ROLES
from .run import OutputLockedError, Preflight
from .run.calibrate import update_calibration
from .run.preflight import fit_calibration, recalibrated

CALIBRATION_ISSUE_CODES = (
    "calibration_unreadable",
    "calibration_target_not_found",
    "calibration_differs_from_header",
    "calibration_axis_absent",
)
"""The calibration's own messages, shown on its card."""

UNIT_OPTIONS = [
    ("µm (micrometres)", "micrometer"),
    ("nm (nanometres)", "nanometer"),
    ("mm (millimetres)", "millimeter"),
    ("pixels: deliberately uncalibrated", PIXELS),
]
AXES_OPTIONS = [("X and Y", "XY"), ("X", "X"), ("Y", "Y"), ("Z", "Z"), ("X, Y and Z", "XYZ")]


class _CalibrationCard:
    """Step 1's calibration card: the recorded pixel sizes and the statements."""

    def _build_calibration_card(self) -> None:
        """Build the card, hidden until a folder is checked."""
        self.calibration_target = widgets.Combobox(
            value=".", placeholder=". (the whole experiment), a folder or an image"
        )
        self.calibration_axes = widgets.Dropdown(options=AXES_OPTIONS, value="XY")
        self.calibration_size = widgets.Text(placeholder="e.g. 0.65")
        self.calibration_unit = widgets.Dropdown(options=UNIT_OPTIONS, value="micrometer")
        self.calibration_state = ui.button(" State this size", style="primary", icon="ruler")
        self.calibration_remove = ui.button(" Remove the statement", icon="times")
        self.calibration_table = widgets.HTML()
        self.calibration_message = widgets.HTML()
        self.calibration_issues = widgets.HTML()
        form = widgets.GridBox(
            [
                widgets.HTML("<b>For</b>"),
                widgets.HTML("<b>Axes</b>"),
                widgets.HTML("<b>Size</b>"),
                widgets.HTML("<b>Unit</b>"),
                self.calibration_target,
                self.calibration_axes,
                self.calibration_size,
                self.calibration_unit,
            ],
            layout=widgets.Layout(
                grid_template_columns="2fr 1fr 1fr 2fr", grid_gap="4px 12px", **_WIDE
            ),
        )
        self.calibration_card = ui.card(
            "Pixel sizes (calibration)",
            [
                ui.help_text(
                    "The size of one pixel along each axis, and where it comes from: the "
                    "image metadata (<i>header</i>), a size you state here (<i>stated</i>), "
                    "or <i>pixels</i> (deliberately uncalibrated). Nothing is guessed: an image "
                    "without either is uncalibrated. A size you state is saved in "
                    f"<b>{CALIBRATION_FILENAME}</b> in the output folder, used instead of the "
                    "metadata, and recorded by the run. No histogram depends on it."
                ),
                self.calibration_table,
                self.calibration_issues,
                widgets.HTML("<b>State a size</b>"),
                ui.help_text(
                    "For <b>.</b> (the whole experiment), a folder or an image, relative to the "
                    "experiment folder. The most specific statement applies. Y and X are "
                    "always stated by name and never exchanged."
                ),
                form,
                widgets.HBox([self.calibration_state, self.calibration_remove]),
                self.calibration_message,
            ],
            visible=False,
        )
        self.calibration_unit.observe(lambda _: self._calibration_unit_changed(), names="value")
        self.calibration_state.on_click(lambda _: self._state_size())
        self.calibration_remove.on_click(lambda _: self._remove_size())

    def _calibration_unit_changed(self) -> None:
        """Disable the size for ``pixels``, which states no value."""
        self.calibration_size.disabled = self.calibration_unit.value == PIXELS

    def _shown_calibration(self, output: Path | None = None) -> tuple[Preflight, dict | None]:
        """Return the preflight with its sizes as the run records them, and the roles.

        Before the axis roles are confirmed, the sizes the folder check found and no roles;
        after, the sizes fitted to the confirmed roles (no Z or T size for an image without
        that axis, with ``calibration_axis_absent``), as the run records them. With
        ``output``, ``calibration.csv`` is read again from that folder first, as the run
        reads it (it may have been edited, or the output folder changed, since the check).
        """
        pre = self.preflight if output is None else recalibrated(self.preflight, output)
        if self.confirmed_mapping is None:
            return pre, None
        review = review_axes(
            pre.metadata,
            self.confirmed_mapping,
            missing_as_singleton=self.missing_as_singleton.value,
        )
        roles = {path: mapping.roles for path, mapping in review.file_mappings.items()}
        return fit_calibration(pre, review)[0], roles

    def _show_calibration(self) -> None:
        """Show every image's recorded sizes and the calibration's messages."""
        pre = self.preflight
        ui.set_visible(self.calibration_card, pre is not None and bool(pre.metadata.files))
        if pre is None:
            return
        self.calibration_target.options = self._calibration_targets()
        shown, roles = self._shown_calibration()
        rows = [("Images", *SIZE_ROLES)]
        for texts, paths in size_rows(shown.calibration.sizes, roles):
            first = paths[0] if len(paths) == 1 else f"{len(paths):,} images, e.g. {paths[0]}"
            rows.append((first, *texts))
        note = (
            "Z and T sizes are those of the image metadata until the axis roles are confirmed "
            "in step 2; then an image without a Z or T axis records no size for it."
            if roles is None
            else "With the confirmed axis roles, as the run records them."
        )
        self.calibration_table.value = ui.table(rows) + ui.help_text(note).value
        issues = [i for i in shown.metadata.issues if i.code in CALIBRATION_ISSUE_CODES]
        self.calibration_issues.value = issues_html(issues) if issues else ""

    def _calibration_targets(self) -> list[str]:
        """Return the targets offered: the whole experiment, every folder and every image."""
        images = self.preflight.discovery.images
        folders = {
            "/".join(parts[:end])
            for parts in (image.split("/") for image in images)
            for end in range(1, len(parts))
        }
        return [".", *sorted(folders), *sorted(images)]

    def _state_size(self) -> None:
        """State the size in the form for its target and axes."""
        target, unit = self.calibration_target.value.strip(), self.calibration_unit.value
        size = None if unit == PIXELS else self.calibration_size.value.strip()
        axes = tuple(self.calibration_axes.value)
        self._change_calibration(
            lambda calibration: calibration.stating(
                calibration_entry(target, axis, size, unit) for axis in axes
            )
        )

    def _remove_size(self) -> None:
        """Remove the statements for the form's target and axes: the metadata are used again."""
        target, axes = self.calibration_target.value.strip(), tuple(self.calibration_axes.value)
        self._change_calibration(lambda calibration: calibration.without(target, axes))

    def _change_calibration(self, change: Callable[[Calibration], Calibration]) -> None:
        """Write a change to ``calibration.csv`` under the lock, then show the new sizes."""
        output = self._design_folder()
        if output is None or self.preflight is None:
            self.calibration_message.value = ui.status(
                "warn", "Check the experiment folder with an output folder outside it first."
            )
            return
        try:
            change(Calibration())  # refuse a wrong statement before taking the lock
            with self.log.attached():
                _, written = update_calibration(output, change)
        except OutputLockedError:
            self.folder_lock.show(output)
            self.calibration_message.value = ui.status("error", "Nothing was changed.")
            return
        except (CalibrationFormatError, OSError, ValueError) as exc:
            self.calibration_message.value = ui.status("error", "Nothing was changed.", [str(exc)])
            return
        finally:
            self._show_log()
        self._calibration_written(output, written)

    def _calibration_written(self, output: Path, written: Path | None) -> None:
        """Read the calibration again and update everything that shows the sizes."""
        self.folder_lock.hide()
        self.preflight = recalibrated(self.preflight, output)
        self.calibration_message.value = (
            ui.status("info", "Nothing changed: the statements were already so.")
            if written is None
            else ui.status(
                "ok",
                f"Saved in {written}.",
                ["The next run records these sizes; no histogram is recomputed."],
            )
        )
        self._show_issue_summary()
        self._show_calibration()
        self._refresh_run_step()
