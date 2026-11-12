"""The notebook's pixel size card (design v0.4 §3, PP-10; one line since the redesign).

By default the card is one line: the one pixel size of the whole experiment and where it
comes from (:mod:`.pixel_size_summary`), with one input when nothing gives a size, or behind
**Change the pixel size…** when the images have one (owner, 10 November 2026). When the images
do not agree the line says how many images have which size and where, and **Set per imaging
unit…** opens; nothing is ever chosen silently. The per-unit part shows the sizes each image
is recorded with, and their source: the image
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
from .calibration import (
    CALIBRATION_FILENAME,
    EXPERIMENT,
    Calibration,
    CalibrationEntry,
    calibration_entry,
)
from .calibration_io import CalibrationFormatError, read_calibration
from .notebook_options import _WIDE
from .notebook_render import issues_html
from .notebook_values import size_rows
from .pixel_size_summary import combination_rows, summary_line, unit_rows, xy_groups
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


def replaced_by_one_size(
    calibration: Calibration, new: tuple[CalibrationEntry, ...]
) -> tuple[CalibrationEntry, ...]:
    """Return the X and Y statements that stating ``new`` for the whole experiment replaces.

    Every X or Y statement of a single folder or image is removed (it would otherwise still
    win), and a whole-experiment X or Y statement is replaced when it says something else.
    """
    wanted = {(entry.axis, entry.size, entry.unit) for entry in new}
    return tuple(
        entry
        for entry in calibration.entries
        if entry.axis in ("X", "Y")
        and (entry.target != EXPERIMENT or (entry.axis, entry.size, entry.unit) not in wanted)
    )


def _xy_words(new: tuple[CalibrationEntry, ...]) -> str:
    """Return the X and Y sizes about to be stated: one size when they are equal."""
    x, y = new[0].describe(), new[1].describe()
    return f"{x} (X and Y)" if x == y else f"X {x} and Y {y}"


def _statement_words(entry: CalibrationEntry) -> str:
    """Return one statement as the confirmation lists it: ``drug/rep1: X 0.108 micrometer``."""
    return f"{entry.target}: {entry.axis} {entry.describe()}"


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
        self._build_pixel_line()
        self._build_pixel_units(form)
        self.calibration_card = ui.card(
            "Pixel size",
            [
                self.pixel_line,
                self.pixel_differs,
                self.pixel_change,
                self.pixel_form,
                self.pixel_replace_box,
                self.calibration_message,
                self.calibration_issues,
                self.pixel_units,
            ],
            visible=False,
        )
        self.calibration_unit.observe(lambda _: self._calibration_unit_changed(), names="value")
        self.calibration_state.on_click(lambda _: self._state_size())
        self.calibration_remove.on_click(lambda _: self._remove_size())

    def _build_pixel_units(self, form: widgets.Widget) -> None:
        """Build the collapsed part: the sizes per imaging unit and the form to state one."""
        self.pixel_units = ui.accordion(
            "Set per imaging unit…",
            [
                widgets.VBox(
                    [
                        ui.help_text(
                            "An imaging unit is a folder of images. Choose it under <b>For</b> "
                            "(<b>.</b> is the whole experiment; an image can be chosen too); "
                            "the most specific statement applies. A stated size is saved in "
                            f"<b>{CALIBRATION_FILENAME}</b> in the output folder and recorded "
                            "by the run; no histogram depends on it."
                        ),
                        self.unit_table,
                        form,
                        widgets.HBox([self.calibration_state, self.calibration_remove]),
                        self.calibration_table,
                    ]
                )
            ],
        )

    def _build_pixel_line(self) -> None:
        """Build the one line about the pixel size and the one input for the whole experiment."""
        self.pixel_line = widgets.HTML()
        self.pixel_differs = widgets.HTML()
        self.unit_table = widgets.HTML()
        narrow = widgets.Layout(width="110px")
        self.pixel_x = widgets.Text(placeholder="e.g. 0.325", layout=narrow)
        self.pixel_y = widgets.Text(placeholder="e.g. 0.325", layout=narrow)
        self.pixel_size = self.pixel_x  # the name the form had with one input
        self.pixel_same = widgets.Checkbox(
            value=True,
            description="same for X and Y",
            indent=False,
            layout=widgets.Layout(width="auto"),
        )
        self.pixel_unit = widgets.Dropdown(
            options=UNIT_OPTIONS, value="micrometer", layout=widgets.Layout(width="230px")
        )
        self.pixel_apply = ui.button(
            " Use for all images",
            style="primary",
            icon="check",
            tooltip="State this size of one pixel (X and Y) for every image of the experiment",
        )
        self.pixel_form = widgets.HBox(
            [
                widgets.HTML("<b>Pixel size&nbsp; X</b>&nbsp;"),
                self.pixel_x,
                widgets.HTML("&nbsp;<b>Y</b>&nbsp;"),
                self.pixel_y,
                self.pixel_unit,
                self.pixel_same,
                self.pixel_apply,
            ],
            layout=widgets.Layout(align_items="center", flex_flow="row wrap"),
        )
        self.pixel_x.observe(lambda _: self._pixel_xy_changed(), names="value")
        self.pixel_same.observe(lambda _: self._pixel_xy_changed(), names="value")
        self._pixel_xy_changed()
        self.pixel_change = ui.button(
            " Change the pixel size…", icon="pencil", tooltip="State one size for all images"
        )
        self.pixel_change.on_click(lambda _: self._toggle_pixel_form())
        self.pixel_apply.on_click(lambda _: self._state_for_all())
        self.pixel_unit.observe(lambda _: self._pixel_unit_changed(), names="value")
        self._build_pixel_replace()

    def _build_pixel_replace(self) -> None:
        """Build the confirmation shown before one size replaces what gives the sizes now."""
        self._pending_for_all: tuple | None = None
        self.pixel_replace_text = widgets.HTML()
        self.pixel_replace_yes = ui.button(" Replace them", style="warning", icon="check")
        self.pixel_replace_no = ui.button(" Cancel", icon="times")
        self.pixel_replace_box = widgets.VBox(
            [
                self.pixel_replace_text,
                widgets.HBox([self.pixel_replace_yes, self.pixel_replace_no]),
            ]
        )
        ui.set_visible(self.pixel_replace_box, False)
        self.pixel_replace_yes.on_click(lambda _: self._confirm_for_all())
        self.pixel_replace_no.on_click(lambda _: self._cancel_for_all())
        for box in (self.pixel_x, self.pixel_y, self.pixel_same, self.pixel_unit):
            box.observe(lambda _: self._cancel_for_all(), names="value")

    def _pixel_unit_changed(self) -> None:
        """Disable the sizes for ``pixels``, which states no value."""
        self._pixel_xy_changed()

    def _pixel_xy_changed(self) -> None:
        """Keep Y as X while *same for X and Y* is ticked (Y is then not typed)."""
        pixels = self.pixel_unit.value == PIXELS
        same = self.pixel_same.value
        if same and self.pixel_y.value != self.pixel_x.value:
            self.pixel_y.value = self.pixel_x.value
        self.pixel_x.disabled = pixels
        self.pixel_y.disabled = pixels or same

    def _toggle_pixel_form(self) -> None:
        ui.set_visible(self.pixel_form, not ui.is_visible(self.pixel_form))

    def _state_for_all(self) -> None:
        """State one X and Y size for the whole experiment, once the user saw what it replaces.

        The user's explicit statement for all images: statements for single folders or images
        would otherwise still apply to them (the most specific wins), so they are removed
        along X and Y; Z statements are kept. Before anything is written, the statements it
        removes or replaces and the sizes the images have now are shown, and only **Replace
        them** writes; with nothing to replace it is written at once. Header sizes that differ
        are then named by the ``calibration_differs_from_header`` warning, as for any statement.
        """
        self._cancel_for_all()
        unit = self.pixel_unit.value
        typed = {"X": self.pixel_x.value.strip(), "Y": self.pixel_y.value.strip()}
        if self.pixel_same.value:
            typed["Y"] = typed["X"]
        try:
            new = tuple(
                calibration_entry(EXPERIMENT, axis, None if unit == PIXELS else typed[axis], unit)
                for axis in ("X", "Y")
            )
            current = self._calibration_statements()
        except (CalibrationFormatError, OSError, ValueError) as exc:
            self.calibration_message.value = ui.status("error", "Nothing was changed.", [str(exc)])
            return
        replaced = replaced_by_one_size(current, new)
        overridden = self._overridden_sizes(new)
        if not replaced and not overridden:
            self._write_for_all(new, replaced)
            return
        self._pending_for_all = (new, replaced)
        lines = [f"Statements removed or replaced: {_statement_words(e)}" for e in replaced]
        lines += [f"The images have now: {line}" for line in overridden]
        self.pixel_replace_text.value = ui.status(
            "warn",
            f"Use {_xy_words(new)} for all images? This replaces what gives "
            "their size now; nothing is written until you click Replace them.",
            lines,
        )
        ui.set_visible(self.pixel_replace_box, True)

    def _calibration_statements(self) -> Calibration:
        """Return the statements ``calibration.csv`` holds now (read only, without the lock)."""
        output = self._design_folder()
        path = None if output is None else output / CALIBRATION_FILENAME
        return read_calibration(path)[0] if path is not None and path.is_file() else Calibration()

    def _overridden_sizes(self, new: tuple[CalibrationEntry, ...]) -> list[str]:
        """Return the line about the X and Y sizes the images have now, when one differs."""
        if self.preflight is None:
            return []
        groups = xy_groups(self._shown_calibration()[0].calibration.sizes)
        stated = [(entry.size, None if entry.in_pixels else entry.unit) for entry in new]
        differs = any(
            (size.value, size.unit) != wanted
            for group in groups
            if group.calibrated
            for size, wanted in zip((group.x, group.y), stated, strict=True)
        )
        return [summary_line(groups)[1]] if differs else []

    def _confirm_for_all(self) -> None:
        """Write the one size shown, unless the statements changed since they were shown."""
        pending = self._pending_for_all
        self._cancel_for_all()
        if pending is not None:
            self._write_for_all(*pending)

    def _cancel_for_all(self) -> None:
        """Take back the confirmation: nothing is written."""
        self._pending_for_all = None
        if getattr(self, "pixel_replace_box", None) is not None:
            ui.set_visible(self.pixel_replace_box, False)

    def _write_for_all(
        self, new: tuple[CalibrationEntry, ...], replaced: tuple[CalibrationEntry, ...]
    ) -> None:
        """Write ``new`` for the whole experiment, removing exactly the ``replaced`` shown."""

        def change(calibration: Calibration) -> Calibration:
            if replaced_by_one_size(calibration, new) != replaced:
                raise ValueError(
                    f"{CALIBRATION_FILENAME} changed since its statements were shown: click "
                    "Use for all images again to see them"
                )
            kept = Calibration(tuple(e for e in calibration.entries if e.axis not in ("X", "Y")))
            return kept.stating(new)

        if self._change_calibration(change, validate=False) and replaced:
            self.calibration_message.value += ui.status(
                "info", "Replaced: " + "; ".join(_statement_words(e) for e in replaced)
            )

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
        """Show the pixel size in one line, the per-unit sizes and the calibration's messages."""
        pre = self.preflight
        self._cancel_for_all()  # a confirmation shown is of the sizes before this change
        ui.set_visible(self.calibration_card, pre is not None and bool(pre.metadata.files))
        if pre is None:
            return
        self.calibration_target.options = self._calibration_targets()
        shown, roles = self._shown_calibration()
        sizes = shown.calibration.sizes
        groups = xy_groups(sizes)
        kind, line = summary_line(groups)
        self.pixel_line.value = ui.status(kind, line)
        self.pixel_differs.value = self._differing_sizes(groups)
        one_size = len(groups) == 1 and groups[0].calibrated
        # One input when nothing gives a size; behind "Change…" when the images have one.
        ui.set_visible(self.pixel_change, one_size)
        ui.set_visible(self.pixel_form, not one_size)
        if len(groups) > 1 and not self._one_line_sizes:
            self.pixel_units.selected_index = 0  # they differ: the units are shown, not hidden
        rows = [("Imaging unit (folder)", "Pixel size (X × Y)", "From")]
        self.unit_table.value = ui.table([*rows, *unit_rows(sizes)])
        rows = [("Images", *SIZE_ROLES)]
        for texts, paths in size_rows(sizes, roles):
            first = paths[0] if len(paths) == 1 else f"{len(paths):,} images, e.g. {paths[0]}"
            rows.append((first, *texts))
        note = (
            "Z and T sizes are those of the image metadata until the axis roles are confirmed "
            "in step 2; then an image without a Z or T axis records no size for it."
            if roles is None
            else "With the confirmed axis roles, as the run records them."
        )
        self.calibration_table.value = ui.help_text(note).value + ui.table(rows)
        issues = [i for i in shown.metadata.issues if i.code in CALIBRATION_ISSUE_CODES]
        self.calibration_issues.value = issues_html(issues) if issues else ""

    _one_line_sizes = False
    """True in the shipped notebook: sizes that differ are one line, a folded table and the
    two explicit choices, and the per-unit part stays folded until it is opened."""

    def _differing_sizes(self, groups: list) -> str:
        """Return, for sizes that differ, the folded table and the choices (HTML), else ""."""
        if len(groups) < 2 or not self._one_line_sizes:
            return ""
        rows = [("Pixel size (X × Y)", "Images", "Folders", "From"), *combination_rows(groups)]
        table = (
            f"<details class='mb-about'><summary>The {len(groups)} different sizes</summary>"
            f"{ui.table(rows)}</details>"
        )
        choices = (
            "<div style='color:#475569;font-size:12.5px;margin:2px 0 6px 0'>Nothing is chosen "
            "for you. Until you choose, each image is "
            "recorded with the size of its own header. Either state <b>one size for all "
            "images</b> below (recorded as stated by you; the headers stay as they are), or "
            "state sizes per folder under <b>Set per imaging unit…</b>.</div>"
        )
        return table + choices

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

    def _change_calibration(
        self, change: Callable[[Calibration], Calibration], *, validate: bool = True
    ) -> bool:
        """Write a change to ``calibration.csv`` under the lock, then show the new sizes.

        With ``validate``, the change is first tried on no statements, so that a wrong
        statement is refused before the lock is taken. Returns False when nothing was written
        because the change was refused (the message says why).
        """
        output = self._design_folder()
        if output is None or self.preflight is None:
            self.calibration_message.value = ui.status(
                "warn", "Check the experiment folder with an output folder outside it first."
            )
            return False
        try:
            if validate:
                change(Calibration())  # refuse a wrong statement before taking the lock
            with self.log.attached():
                _, written = update_calibration(output, change)
        except OutputLockedError:
            self.folder_lock.show(output)
            self.calibration_message.value = ui.status("error", "Nothing was changed.")
            return False
        except (CalibrationFormatError, OSError, ValueError) as exc:
            self.calibration_message.value = ui.status("error", "Nothing was changed.", [str(exc)])
            return False
        finally:
            self._show_log()
        self._calibration_written(output, written)
        return True

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
