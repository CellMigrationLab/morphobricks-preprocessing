"""Step 3 of the notebook: **Describe the masks**, the optional mask step (plan §10, PP-10d).

After a run that recorded masks, the button runs the command line's ``masks`` step with the
same functions (:func:`.run.mask_step.prepare_mask_step`, :func:`.run.mask_step.describe_masks`)
and the inputs the run recorded: the experiment folder, the masks folder, the mask axes and
the exclusions. It reads the masks, writes ``mask_relationships.json`` to the output folder
holding its lock, and shows each mask type, the relationships between the types and their
hierarchy, as suggestions to check (contract §15). The thresholds are the defaults; the
command line's ``masks`` can change them. A refusal is shown on the card (the locked output
folder with :class:`.notebook_output._LockCard`), and an embedding brick that hides the
output folder (``show_output=False``) does not get the card.
"""

from __future__ import annotations

import html
import traceback

import ipywidgets as widgets

from . import notebook_style as ui
from .mask_scan import MASK_REPORT_FILENAME
from .mask_text import (
    mask_hierarchy_lines,
    mask_relationship_rows,
    mask_thresholds_used,
    mask_type_rows,
)
from .notebook_output import _LockCard
from .notebook_render import issues_html
from .run import MaskStep, MaskStepResult, mask_step
from .validation import has_errors, plural

_STALE = object()
"""Marks the step as describing no run (a run is going on)."""
_LABEL = " Describe the masks"
_ICON = "sitemap"
_SUGGESTIONS = (
    "These are suggestions from the masks' pixels: name each mask, then check the "
    "relationships before using them."
)


class _MaskStepCard:
    """Step 3's optional mask step: the button, its progress and the description."""

    def _build_mask_step_card(self) -> None:
        """Build the card, its button disabled until a run records masks."""
        self._mask_step_run: object = _STALE
        self.mask_step_button = ui.button(
            _LABEL,
            style="info",
            icon=_ICON,
            tooltip="Read the masks the run recorded and describe how they relate",
            main=True,
        )
        self.mask_step_hint = widgets.HTML()
        self.mask_step_progress = widgets.IntProgress(
            value=0, min=0, max=1, bar_style="info", layout=widgets.Layout(width="99%")
        )
        ui.set_visible(self.mask_step_progress, False)
        self.mask_step_status = widgets.HTML()
        self.mask_step_lock = _LockCard("Then click Describe the masks again.")
        self.mask_step_result = widgets.HTML()
        self.mask_step_card = ui.card(
            "Describe the masks (optional)",
            [
                ui.help_text(
                    "Reads every mask the run recorded and describes each mask type (one "
                    "object per label, or labels in several pieces), how the types relate "
                    "(inside, grouped by, the same objects) and their hierarchy, with the "
                    f"counts behind each. Writes <b>{MASK_REPORT_FILENAME}</b> to the output "
                    "folder, as the command line's masks step does. The relationship "
                    "thresholds are the defaults; the command line's masks step can change "
                    "them. Optional: the histogram table does not need it."
                ),
                self.mask_step_hint,
                self.mask_step_button,
                self.mask_step_progress,
                self.mask_step_status,
                self.mask_step_lock.card,
                self.mask_step_result,
            ],
        )
        self.mask_step_button.on_click(lambda _: self._on_describe_masks())

    def _refresh_mask_step(self) -> None:
        """Offer the step only after a run that recorded masks; clear it for another run."""
        ui.set_visible(self.mask_step_card, self._show_output)
        if self.result is self._mask_step_run:
            return
        self._mask_step_run = self.result
        self._clear_mask_step()
        types = self._recorded_mask_types()
        self.mask_step_button.disabled = not types
        if types:
            hint = (
                f"The run recorded {plural(len(types), 'mask type')}: "
                f"<b>{html.escape(', '.join(types))}</b>."
            )
        elif self.result is not None and self.result.written:
            hint = "The run recorded no masks: give a masks folder in step 1 and run again."
        else:
            hint = "Available after a run that recorded masks (step 1's masks folder)."
        self.mask_step_hint.value = f"<div class='mb-help'>{hint}</div>"

    def _recorded_mask_types(self) -> tuple[str, ...]:
        """Return the mask types the last run recorded (none before a written run)."""
        if self.result is None or not self.result.written:
            return ()
        with self.log.attached():
            return mask_step.recorded_mask_types(self.result.output_folder)

    def _mask_step_stale(self) -> None:
        """Clear the step and disable it while a run replaces the results it describes."""
        self._mask_step_run = _STALE
        self._clear_mask_step()
        self.mask_step_button.disabled = True

    def _clear_mask_step(self) -> None:
        """Clear the progress, status and description of an earlier click."""
        ui.set_visible(self.mask_step_progress, False)
        self.mask_step_status.value = self.mask_step_result.value = ""
        self.mask_step_lock.hide()

    def _on_describe_masks(self) -> MaskStepResult | None:
        """Describe the masks the last run recorded and write ``mask_relationships.json``.

        Returns
        -------
        MaskStepResult or None
            None when the button is disabled or the step was refused or failed (the card
            says why).
        """
        self._refresh_mask_step()
        if self.mask_step_button.disabled:
            return None
        self._busy(self.mask_step_button, "Reading the masks…")
        self._clear_mask_step()
        self.mask_step_status.value = ui.working("Pairing the masks the run recorded…")
        try:
            with self.log.attached():
                return self._describe_recorded_masks()
        except Exception as exc:  # unexpected failures still get a readable message
            self.log.lines.append(traceback.format_exc())
            self._mask_step_failed(
                "The masks could not be described because of an unexpected problem.",
                [f"{type(exc).__name__}: {exc}", "Open Technical details for the full report."],
            )
            return None
        finally:
            self._idle(self.mask_step_button, _LABEL, _ICON)
            self._show_log()

    def _describe_recorded_masks(self) -> MaskStepResult | None:
        """Pair, read and describe the masks with the run's recorded inputs; show the result."""
        step = mask_step.prepare_mask_step(None, self.result.output_folder)
        if not step.ready:
            self._show_mask_step_refused(step)
            return None
        ui.set_visible(self.mask_step_progress, True)
        self.mask_step_progress.value, self.mask_step_progress.bar_style = 0, "info"
        try:
            result = mask_step.describe_masks(step, progress=self._mask_step_progressed)
        except ValueError as exc:  # masks whose pixels cannot be used (planes, labels)
            self._mask_step_failed(
                "The masks cannot be used.",
                [str(exc), "Correct or replace the mask named, run step 3 again, then retry."],
            )
            return None
        if result.refusal is not None:
            self.mask_step_progress.bar_style = "danger"
            self.mask_step_status.value = ui.status(
                "error",
                "The description was not written: the output folder is locked.",
                ["The masks were read, but nothing was written."],
            )
            self.mask_step_lock.show(step.output_folder)
            return None
        self.mask_step_progress.bar_style = "success"
        self._show_mask_description(step, result)
        return result

    def _mask_step_progressed(self, done: int, total: int, image: str) -> None:
        self.mask_step_progress.max = max(total, 1)
        self.mask_step_progress.value = done
        if image:
            self.mask_step_status.value = ui.working(
                f"Reading the masks of image {done + 1} of {total}: {image}"
            )

    def _mask_step_failed(self, title: str, lines: list[str]) -> None:
        """Show a failure as a red card; nothing was written."""
        self.mask_step_progress.bar_style = "danger"
        self.mask_step_status.value = ui.status("error", title, [*lines, "Nothing was written."])

    def _show_mask_step_refused(self, step: MaskStep) -> None:
        """Say why the masks cannot be read: no recorded experiment, mask errors or no masks."""
        if step.refusal is not None:
            title, issues = "The run's experiment folder is not recorded.", [step.refusal]
        elif has_errors(step.issues):
            title, issues = "The masks have errors: none was read.", list(step.issues)
        else:
            title, issues = "No masks were found with the inputs the run recorded.", []
        self.mask_step_status.value = ui.status(
            "error", title, ["Nothing was written. Read the messages below; each says what to do."]
        )
        self.mask_step_result.value = issues_html(issues) if issues else ""

    def _show_mask_description(self, step: MaskStep, result: MaskStepResult) -> None:
        """Show the mask types, relationships and hierarchy, and where the report is."""
        report = result.report
        self.mask_step_status.value = ui.status(
            "ok",
            f"The masks are described: {plural(len(report.types), 'mask type')} in "
            f"{plural(report.images, 'image')}.",
            [
                f"Written: <b>{html.escape(str(result.path))}</b>",
                f"Thresholds: {html.escape(mask_thresholds_used(report))}.",
                f"<b>{html.escape(_SUGGESTIONS)}</b>",
            ],
            raw=True,
        )
        relationships = mask_relationship_rows(report)
        same = "".join(
            f"<li>{html.escape(first)} and {html.escape(second)}</li>"
            for first, second in report.same_objects
        )
        hierarchy = html.escape("\n".join(mask_hierarchy_lines(report)))
        parts = [
            "<b>Mask types (suggestions to check)</b>",
            ui.table(mask_type_rows(report), numeric_from=2),
            "<b>Relationships found (suggestions to check)</b>",
            ui.table(relationships, numeric_from=3)
            if len(relationships) > 1
            else "<div class='mb-help'>None: no mask type is inside or grouped by another.</div>",
        ]
        if same:
            parts += ["<b>Two segmentations of the same objects (to check)</b>", f"<ul>{same}</ul>"]
        parts += ["<b>Hierarchy (direct parents only)</b>", f"<pre>{hierarchy}</pre>"]
        if step.issues:
            parts.append(issues_html(step.issues))
        self.mask_step_result.value = "".join(parts)
