"""Step 2 of the notebook: **Make named-axis copies**, the conversion card (PP-11b, §17).

When the axis check of step 2 reports images whose header names their axes wrongly
(``axes_yx_swapped``, ``axes_contradict_header``, ``axes_x_before_y``) or not at all
(``axes_not_named``), this card offers what the command line's ``convert`` does, with the
same functions: :func:`.conversion_plan.preview_conversion` (the folder rule and the plan,
from the headers only) and :func:`.conversion.convert_experiment` (the copies, under the
copy folder's lock).

- The folder for the copies is required (no default): pasted or chosen with **Browse…**
  (on Google Colab, the Files panel's **Copy path**), outside the experiment and not
  holding it.
- The roles are the user's: those confirmed with **Confirm axis roles**, or, while the
  axis errors keep that button disabled, the roles shown once the preview is checked and
  the card's own question is answered. Nothing is taken from the headers.
- Before anything is written the card shows which images are written as OME-TIFF and
  which are copied byte for byte, the space needed and the free space.
- A refusal is shown with the existing cards (the locked copy folder with
  :class:`.notebook_output._LockCard`), and after the copies are made, the record and the
  next step: check the copies in step 1 (one button fills the experiment folder, keeps the
  output folder and checks again).

An embedding brick that hides the output folder (``show_output=False``) does not get it.
"""

from __future__ import annotations

import html
import traceback
from pathlib import Path

import ipywidgets as widgets

from . import notebook_style as ui
from .axes import AxisMapping, AxisReview, axis_name
from .axis_check import AXIS_CHECK_CODES
from .conversion import ConversionResult, convert_experiment
from .conversion_plan import ConversionPreview, preview_conversion
from .conversion_record import RECORD_FILENAME
from .notebook_options import _WIDE
from .notebook_output import _LockCard
from .notebook_render import issues_html
from .planes import format_bytes
from .validation import plural

_LABEL = " Make named-axis copies"
_ICON = "copy"
_RECHECK_LABEL = " Check the copies in step 1"
_COPY_HOLDERS = (
    "Another conversion is writing into this folder, or one was stopped (for example Colab "
    "disconnected) and left its lock behind. Wait for a conversion that is still going."
)


class _ConvertCard:
    """Step 2's conversion card: the folder, the roles, the plan, the copies, what next."""

    def _build_convert_card(self) -> None:
        """Build the card, hidden until the axis check reports images to convert."""
        self._convert_preview: ConversionPreview | None = None
        self._converted_to: Path | None = None
        self.convert_folder = widgets.Text(
            placeholder="a new folder outside the experiment, e.g. D:/microscopy/exp_named_axes",
            continuous_update=False,
            layout=widgets.Layout(flex="1 1 auto", width="auto"),
        )
        self.convert_browse = ui.button(
            "Browse…", icon="folder-open", tooltip="Choose the folder for the copies in a dialog"
        )
        self.convert_roles = widgets.HTML()
        self.convert_roles_checked = ui.ConfirmToggle(
            "Are these the roles of every image, for the copies?",
            "The copies are written with exactly these roles, and their metadata then names "
            "every axis. Check them with the preview first: the image header is not used.",
            "Yes, write the copies with these roles",
            "Roles confirmed for the copies.",
            "Undo: the roles are not right",
        )
        self.convert_plan = widgets.HTML()
        self.convert_button = ui.button(
            _LABEL,
            style="success",
            icon=_ICON,
            tooltip="Write the copies into the folder above (the originals are only read)",
            main=True,
        )
        self.convert_progress = widgets.IntProgress(
            value=0, min=0, max=1, bar_style="info", layout=widgets.Layout(width="99%")
        )
        self.convert_status = widgets.HTML()
        self.convert_lock = _LockCard(
            "Then click Make named-axis copies again.",
            "The folder for the copies is locked",
            _COPY_HOLDERS,
            "Another conversion",
        )
        self.convert_result = widgets.HTML()
        self.convert_recheck_button = ui.button(
            _RECHECK_LABEL,
            style="primary",
            icon="search",
            tooltip="Set step 1's experiment folder to the copies, keep the output folder, "
            "and check again",
            main=True,
        )
        self.convert_card = ui.card("Make named-axis copies (optional)", self._convert_children())
        ui.set_visible(self.convert_card, False)
        self._connect_convert_card()

    def _connect_convert_card(self) -> None:
        """Connect the card's controls, and the step 2 answers its roles depend on."""
        self.convert_browse.on_click(lambda _: self._browse_copy_folder())
        self.convert_folder.observe(lambda _: self._plan_copies(), names="value")
        self.convert_roles_checked.observe(lambda _: self._convert_roles_changed())
        self.preview_checked.observe(lambda _: self._convert_roles_changed())
        self.confirm_button.on_click(lambda _: self._convert_roles_changed())
        self.convert_button.on_click(lambda _: self._on_convert())
        self.convert_recheck_button.on_click(lambda _: self._check_the_copies())

    def _convert_children(self) -> list[widgets.Widget]:
        """Return the card's widgets, top to bottom."""
        return [
            ui.help_text(
                "Saves the images whose metadata names their axes wrongly, or not at all, "
                "again as named-axis OME-TIFF copies with the roles above, in a new folder; "
                "every other image is copied as it is, so the folder holds the whole "
                "experiment with the same folders and file names. The originals are only "
                "read, and masks are never converted. Then you check and run preprocessing "
                "on the copies, with the same output folder."
            ),
            widgets.HBox(
                [self.convert_folder, self.convert_browse], layout=widgets.Layout(**_WIDE)
            ),
            ui.help_text(
                "Required: a new or empty folder, outside the experiment folder (or the folder "
                "of an earlier conversion of this experiment, whose unchanged copies are "
                "kept). Paste the path, or use <b>Browse…</b>; on Google Colab, right-click "
                "a folder in the <b>Files</b> panel, choose <b>Copy path</b> and paste it."
            ),
            self.convert_roles,
            self.convert_roles_checked.widget,
            self.convert_plan,
            self.convert_button,
            self.convert_progress,
            self.convert_status,
            self.convert_lock.card,
            self.convert_result,
            self.convert_recheck_button,
        ]

    def _refresh_convert_card(self, review: AxisReview) -> None:
        """Offer the card when the axis check of these roles finds images to convert.

        Called whenever the roles, the missing-axes answer or the checked folder change:
        the roles must be confirmed again, and an earlier plan or result no longer applies.
        """
        if not hasattr(self, "convert_card"):
            return
        offered = self._show_output and any(i.code in AXIS_CHECK_CODES for i in review.issues)
        ui.set_visible(self.convert_card, offered)
        self.convert_roles_checked.set(False, notify=False)
        self._convert_roles_changed()

    def _convert_roles_changed(self) -> None:
        """Show the roles the copies get, clear an earlier result and plan again."""
        if not hasattr(self, "convert_card"):
            return  # the axis step's own handlers run while the card is being built
        self._clear_conversion()
        roles = self._copy_roles()
        confirmed = self.confirmed_mapping is not None
        self.convert_roles_checked.disabled = not self.preview_checked.value
        ui.set_visible(
            self.convert_roles_checked.widget, not confirmed and self.mapping is not None
        )
        self.convert_roles.value = _roles_html(self.mapping, roles, confirmed)
        self._plan_copies()

    def _copy_roles(self) -> AxisMapping | None:
        """Return the roles the copies are written with, or None until the user confirmed them."""
        if self.confirmed_mapping is not None:
            return self.confirmed_mapping
        if self.convert_roles_checked.value and self.preview_checked.value:
            return self.mapping
        return None

    def _clear_conversion(self) -> None:
        """Clear the progress, status, lock card and result of an earlier conversion."""
        self._converted_to = None
        ui.set_visible(self.convert_progress, False)
        ui.set_visible(self.convert_recheck_button, False)
        self.convert_status.value = self.convert_result.value = ""
        self.convert_lock.hide()

    def _plan_copies(self) -> ConversionPreview | None:
        """Show what the conversion would write, from the headers only; write nothing."""
        self._convert_preview = None
        roles, folder = self._copy_roles(), ui.clean_path(self.convert_folder.value)
        if self.preflight is None or roles is None or not folder:
            self.convert_plan.value = ui.status(
                "info",
                "Waiting for the roles and the folder for the copies.",
                [
                    "Confirm the roles above and give the folder for the copies; the card "
                    "then shows what will be written."
                ],
            )
            self._update_convert_button()
            return None
        with self.log.attached():
            preview = preview_conversion(
                self.preflight.discovery.root,
                roles,
                folder,
                exclude=self.preflight.excluded,
                missing_as_singleton=self.missing_as_singleton.value,
                memory_limit=self._memory_limit(),
            )
        self._convert_preview = preview
        self.convert_plan.value = _plan_html(preview)
        self._update_convert_button()
        return preview

    def _update_convert_button(self) -> None:
        """Enable the button once the plan shows nothing that stops the conversion."""
        preview = self._convert_preview
        ready = preview is not None and preview.ok and (preview.fits or preview.earlier_conversion)
        self.convert_button.disabled = not ready
        self.convert_button.tooltip = (
            "Write the copies into the folder above (the originals are only read)"
            if ready
            else "Confirm the roles and give a folder the copies can be written to first"
        )

    def _browse_copy_folder(self) -> None:
        chosen = ui.ask_directory(ui.clean_path(self.convert_folder.value))
        if chosen is None:
            self.convert_status.value = ui.no_dialog_status("folder", "the folder for the copies")
        elif chosen:
            self.convert_folder.value = chosen

    def _on_convert(self) -> ConversionResult | None:
        """Write the copies with the confirmed roles, under the copy folder's lock.

        Returns
        -------
        ConversionResult or None
            None when the button is disabled or an unexpected problem stopped it (the card
            says why).
        """
        self._clear_conversion()
        preview = self._plan_copies()  # the experiment or the folder may have changed since
        if preview is None or self.convert_button.disabled:
            return None
        self._busy(self.convert_button, "Making the copies…")
        ui.set_visible(self.convert_progress, True)
        self.convert_progress.value, self.convert_progress.bar_style = 0, "info"
        self.convert_status.value = ui.working("Checking the images and the folder…")
        try:
            with self.log.attached():
                result = convert_experiment(
                    self.preflight.discovery.root,
                    self._copy_roles(),
                    preview.destination,
                    exclude=self.preflight.excluded,
                    missing_as_singleton=self.missing_as_singleton.value,
                    memory_limit=self._memory_limit(),
                    progress=self._convert_progressed,
                )
        except Exception as exc:  # unexpected failures still get a readable message
            self.log.lines.append(traceback.format_exc())
            self.convert_progress.bar_style = "danger"
            self.convert_status.value = ui.status(
                "error",
                "The copies could not be made because of an unexpected problem.",
                [f"{type(exc).__name__}: {exc}", "Open Technical details for the full report."],
            )
            return None
        finally:
            self._idle(self.convert_button, _LABEL, _ICON)
            self._show_log()
        self._show_conversion(result)
        return result

    def _convert_progressed(self, done: int, total: int, image: str) -> None:
        self.convert_progress.max = max(total, 1)
        self.convert_progress.value = done
        if image:
            self.convert_status.value = ui.working(f"Copying image {done + 1} of {total}: {image}")

    def _show_conversion(self, result: ConversionResult) -> None:
        """Show a refusal with its card, or the copies, the record and the next step."""
        if not result.written:
            self.convert_progress.bar_style = "danger"
            locked = any(issue.code == "output_locked" for issue in result.issues)
            self.convert_status.value = ui.status(
                "error",
                "No copies were made: the folder for the copies is locked."
                if locked
                else "No copies were made.",
                ["Nothing was written. Read the messages below; each says what to do."],
            )
            if locked:
                self.convert_lock.show(result.destination)
            self.convert_result.value = issues_html(result.issues)
            return
        self.convert_progress.bar_style = "success"
        self._converted_to = result.destination
        output = self._output_path(self.preflight.discovery.root)
        self.convert_status.value = _done_html(result)
        self.convert_result.value = _next_html(result, output, self.missing_as_singleton.value) + (
            issues_html(result.issues) if result.issues else ""
        )
        ui.set_visible(self.convert_recheck_button, True)

    def _check_the_copies(self) -> None:
        """Check the copies in step 1: the copy folder as experiment, the same output folder."""
        if self._converted_to is None or self.preflight is None:
            return
        copies = self._converted_to
        # The run on the copies keeps the output folder (its design, image type and
        # calibration), so an empty box, meaning the originals' default, is filled in.
        self.output.value = str(self._output_path(self.preflight.discovery.root))
        self.folder.value = str(copies)
        self.check_folder()


def _roles_html(mapping: AxisMapping | None, roles: AxisMapping | None, confirmed: bool) -> str:
    """Say which roles the copies get, or what confirms them."""
    if mapping is None:
        return ""
    shown = roles or mapping
    lines = [
        f"{html.escape(axis_name(axis))} in the file → <b>{html.escape(role or 'no role')}</b>"
        for axis, role in zip(shown.axes, shown.roles, strict=True)
    ]
    if roles is None:
        return ui.status(
            "info",
            "The roles for the copies are not confirmed yet.",
            [
                *lines,
                "Check them with Show preview and press Yes, the preview looks right; then "
                "answer the question below.",
            ],
            raw=True,
        )
    how = "confirmed in step 2" if confirmed else "confirmed for the copies"
    return ui.status("ok", f"The copies are written with these roles ({how}).", lines, raw=True)


def _plan_html(preview: ConversionPreview) -> str:
    """Show what the conversion would write, or what stops it."""
    errors = [issue for issue in preview.issues if issue.is_error]
    if errors:
        return ui.status(
            "error",
            "These copies cannot be made.",
            ["Nothing was written. Read the messages below; each says what to do."],
        ) + issues_html(preview.issues)
    converted = [f"{path} ({code})" for path, code in preview.converted]
    copied = [path + (" (excluded)" if why else "") for path, why in preview.copied]
    free = "unknown" if preview.free_bytes is None else format_bytes(preview.free_bytes)
    lines = [
        f"Written as named-axis OME-TIFF with your roles: {plural(len(converted), 'image')}"
        + ui.path_list(converted, limit=5),
        "Copied byte for byte (their metadata names their axes as confirmed, OME-Zarr, or "
        f"excluded): {plural(len(copied), 'image')}" + ui.path_list(copied, limit=5),
        f"Space needed: about {format_bytes(preview.needed_bytes)}. Free on that drive: {free}.",
    ]
    if preview.earlier_conversion:
        lines.append(
            "This folder holds an earlier conversion of this experiment: unchanged copies are "
            "kept, and copies that differ are refused (nothing is overwritten)."
        )
    kind = "info"
    if not preview.fits:
        kind = "warn" if preview.earlier_conversion else "error"
        lines.append(
            "<b>The drive does not have enough free space.</b> Free some space, or choose a "
            "folder on another drive."
        )
    title = f"What will happen in {html.escape(str(preview.destination))}"
    warnings = issues_html(preview.issues) if preview.issues else ""
    return ui.status(kind, title, lines, raw=True) + warnings


def _done_html(result: ConversionResult) -> str:
    """Say what the conversion wrote and where its record is."""
    record = result.destination / RECORD_FILENAME
    return ui.status(
        "ok",
        "The named-axis copies are made.",
        [
            f"Folder: <b>{html.escape(str(result.destination))}</b>",
            f"Written as OME-TIFF with your roles: {plural(len(result.converted), 'image')}",
            f"Copied as they are: {plural(len(result.copied), 'image')}",
            f"Already there and unchanged: {plural(len(result.reused), 'image')}",
            f"Conversion record: <b>{html.escape(str(record))}</b>",
        ],
        raw=True,
    )


def _next_html(result: ConversionResult, output: Path, missing_as_singleton: bool) -> str:
    """Say what to do next: check the copies with the same output folder, and what to redo."""
    lines = [
        f"Press <b>{_RECHECK_LABEL.strip()}</b>: step 1's experiment folder becomes "
        f"<b>{html.escape(str(result.destination))}</b>, the output folder stays "
        f"<b>{html.escape(str(output))}</b>, and the folder is checked again. Then confirm "
        "the labels, the axis roles (the copies' metadata names their axes"
        + (f": <b>{html.escape(result.suggested_axes)}</b>" if result.suggested_axes else "")
        + ") and the image type again. The experiment design and the stated pixel sizes are kept "
        "(same output folder), and the run recomputes every row from the copies.",
    ]
    if missing_as_singleton:
        lines.append(
            "You confirmed that images lacking some axes have a single plane there: the copies "
            "do not add those axes, so press <b>Yes, they have a single plane there</b> again "
            "in step 2 (on the command line, give <b>--missing-as-singleton</b> again)."
        )
    if result.unknown_sizes:
        sizes = [f"{path}: {', '.join(roles)}" for path, roles in result.unknown_sizes.items()]
        lines.append(
            f"Pixel sizes: {plural(len(sizes), 'copy', 'copies')} do not carry a size their "
            "header gave, because the axis was relabelled: its size is unknown in the copy "
            "(a swapped Y and X keep theirs only when the header gives X and Y equal). If you "
            "know them, state them "
            "under <b>Pixel sizes</b> in step 1 after checking the copies."
            + ui.path_list(sizes, limit=5)
        )
    return ui.status(
        "info", "What next: check the copies in step 1, then run on them.", lines, raw=True
    )
