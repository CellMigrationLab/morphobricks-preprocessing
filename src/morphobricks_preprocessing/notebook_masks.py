"""Step 1 of the notebook: the masks folder and the mask axes (design v0.4 §2, §3; PP-10c).

The **Masks folder (optional)** field takes the folder ``check --masks`` and ``run --masks``
take. **Check folder** pairs the masks with the same functions as the command line
(:func:`.run.preflight`, :func:`.masks.find_masks`); the **Masks** card shows the mask
types found, the images without masks and the masks' messages. Masks whose files do not
name their axes need their axes stated once per mask type (never ``YX``: a 2-D mask is not
shared by several planes, owner decision of 5 November 2026), as with
``--mask-axes TYPE=ROLES``: the card asks only for those
(:func:`.mask_review.mask_axes_questions`), with the statements that fit, and applies nothing
until **Check the masks** is clicked. The run is given the same masks folder and
mask axes (:meth:`_RunPlan._given_inputs`). Without a masks folder the run reuses the one
the earlier run in the output folder recorded (design v0.4 §1,
:func:`.run.planned.planned_masks`); **Drop the recorded masks** is the notebook's
``--no-masks``. An embedding brick that hides the output folder (``show_output=False``)
gets neither card, and its check pairs no masks folder, as before.
"""

from __future__ import annotations

import html
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

import ipywidgets as widgets

from . import notebook_style as ui
from .axes import AxisReview
from .axis_review import review_axes
from .discovery import default_output_folder
from .fingerprint import current_fingerprint
from .manifest import MANIFEST_FILENAME
from .mask_checks import mask_issues
from .mask_review import MaskAxesQuestion, mask_axes_questions, mask_type_rows
from .masks import MaskInventory
from .metadata import TiffMetadata
from .notebook_options import _WIDE
from .notebook_progress import Wait
from .notebook_render import issue_count_chips, issues_html
from .run.planned import PlannedMasks, planned_masks
from .run.preflight import remasked
from .run.recorded import GivenInputs
from .validation import ValidationIssue, has_errors, plural

_ROLE_WORDS = {"Y": "height (Y)", "X": "width (X)", "Z": "depth (Z)", "T": "time (T)"}
_NOT_STATED = ""


class _MasksCard:
    """Step 1's masks folder, its masks card and the mask-axis statements."""

    def _build_masks_cards(self) -> None:
        """Build the masks folder card and the masks card (hidden until a check)."""
        self._mask_axes: dict[str, str] = {}
        self._masks_dropped = False
        self._paired_masks: tuple | None = None
        self._mask_choices: dict[str, widgets.Dropdown] = {}
        self._mask_questions: tuple[MaskAxesQuestion, ...] | None = None
        self._planned_cache: tuple[tuple, PlannedMasks] | None = None
        self.masks_folder = widgets.Text(
            placeholder="optional, e.g. D:/microscopy/experiment_2026_10_masks",
            layout=widgets.Layout(flex="1 1 auto", width="auto"),
        )
        self.masks_browse = ui.button(
            "Browse…", icon="folder-open", tooltip="Choose the masks folder in a dialog"
        )
        self.masks_recorded = widgets.HTML()
        self.masks_drop = ui.button(" Drop the recorded masks", style="warning", icon="times")
        ui.set_visible(self.masks_drop, False)
        self.masks_input_card = ui.card(
            "Masks folder (optional)",
            [
                widgets.HBox(
                    [self.masks_folder, self.masks_browse], layout=widgets.Layout(**_WIDE)
                ),
                ui.help_text(
                    "Only if you have masks (segmentations such as cells or nuclei). A folder "
                    "outside the experiment folder, with one subfolder per mask type, each "
                    "laid out like the experiment (<b>masks/cells/control/rep1/image1.tif</b>), "
                    "or the experiment's folders with the mask type after the image name "
                    "(<b>masks/control/rep1/image1_cells.tif</b>). <b>Check folder</b> pairs "
                    "each mask with its image; no mask pixel is read. Leave it empty for no "
                    "masks."
                ),
                self.masks_recorded,
                self.masks_drop,
            ],
        )
        self._build_masks_card()
        self.masks_browse.on_click(lambda _: self._browse(self.masks_folder))
        self.masks_drop.on_click(lambda _: self._toggle_masks_dropped())
        self.masks_folder.observe(lambda _: self._masks_edited(), names="value")

    def _build_masks_card(self) -> None:
        """Build the card of the masks found, their axes and their messages."""
        self.masks_status = widgets.HTML()
        self.masks_table = widgets.HTML()
        self.mask_axes_box = widgets.VBox()
        self.masks_button = ui.button(
            " Check the masks",
            style="info",
            icon="refresh",
            tooltip="Pair the masks again with the masks folder and the mask axes above",
        )
        self.masks_wait = widgets.HTML()
        self.masks_counts = widgets.HTML()
        self.masks_issues = widgets.HTML()
        self.masks_card = ui.card(
            "Masks",
            [
                self.masks_status,
                self.masks_table,
                self.mask_axes_box,
                self.masks_button,
                self.masks_wait,
                self.masks_counts,
                self.masks_issues,
            ],
            visible=False,
        )
        self.masks_button.on_click(lambda _: self._check_masks())

    # Inputs ------------------------------------------------------------------------------

    def _masks_given(self) -> Path | Literal[False] | None:
        """Return the masks folder given to the run: a folder, False (dropped) or None."""
        folder = ui.clean_path(self.masks_folder.value)
        if folder:
            return Path(folder).resolve()
        return False if self._masks_dropped else None

    def _planned_masks(self, root: Path | None = None) -> PlannedMasks | None:
        """Return the masks folder and axes the run would use, or None without a folder."""
        folder = ui.clean_path(self.folder.value)
        if root is None and not folder:
            return None
        root = Path(folder).resolve() if root is None else root
        given = GivenInputs(masks_folder=self._masks_given(), mask_axes=self._mask_axes or None)
        output = Path(self._output_folder() or default_output_folder(root)).resolve()
        # Every refresh of step 3 asks; the earlier run's manifest is read once per version.
        key = (given, output, current_fingerprint(output / MANIFEST_FILENAME))
        if self._planned_cache is None or self._planned_cache[0] != key:
            self._planned_cache = (key, planned_masks(given, output))
        return self._planned_cache[1]

    def _check_masks_arguments(self, root: Path) -> tuple[Path | None, dict | None]:
        """Return the masks folder and axes for a folder check; nothing when embedded."""
        if not self._show_output:
            self._paired_masks = None
            return None, None
        plan = self._planned_masks(root)
        self._paired_masks = _pairing(plan)
        return plan.folder, plan.mask_axes or None

    def _check_masks(self) -> None:
        """Pair the masks again with the folder and the mask axes chosen; keep the rest."""
        if self.preflight is None:
            return
        for mask_type, choice in self._mask_choices.items():
            if choice.value != _NOT_STATED:
                self._mask_axes[mask_type] = choice.value
        root = self.preflight.discovery.root
        plan = self._planned_masks(root)
        output = self._output_folder() or default_output_folder(root)
        wait = Wait(self.masks_wait)
        wait.start("Reading mask headers")
        with self.log.attached():
            self.preflight = remasked(
                self.preflight, plan.folder, plan.mask_axes or None, output, wait.step
            )
        self.masks_wait.value = ""
        self._paired_masks = _pairing(plan)
        self._show_issue_summary()
        self._refresh_run_step()
        self._refresh_masks_screen()
        self._show_log()

    def _masks_edited(self) -> None:
        """Forget the statements made for the old masks folder's mask types; show the change."""
        if self._mask_axes and self.preflight is not None:
            self._mask_axes = {}
        self._refresh_run_step()

    def _toggle_masks_dropped(self) -> None:
        """Drop the masks folder the earlier run recorded, or keep it again (``--no-masks``)."""
        self._masks_dropped = not self._masks_dropped
        self._refresh_run_step()

    def _reset_masks(self) -> None:
        """Forget the statements and the pairing: the experiment folder changed."""
        self._mask_axes = {}
        self._masks_dropped = False
        self._paired_masks = None
        ui.set_visible(self.masks_card, False)

    # Shown -------------------------------------------------------------------------------

    def _mask_issues_now(self, review: AxisReview | None = None) -> list[ValidationIssue]:
        """Return the masks' issues as the run would give them (by the confirmed roles)."""
        masks = None if self.preflight is None else self.preflight.masks
        if masks is None:
            return []
        if review is None and self.confirmed_mapping is not None:
            review = review_axes(
                self.preflight.metadata,
                self.confirmed_mapping,
                missing_as_singleton=self.missing_as_singleton.value,
            )
        if review is None:
            return list(masks.issues)
        return mask_issues(masks, self.preflight.metadata, review)

    def _masks_stale(self, plan: PlannedMasks | None) -> bool:
        """Return True when the masks shown are not those the run would use, or not stated."""
        if self.preflight is None or not self._show_output or plan is None:
            return False
        # A choice is pending when it differs from the statement in use (given or recorded).
        pending = any(
            choice.value not in (_NOT_STATED, plan.mask_axes.get(name))
            for name, choice in self._mask_choices.items()
        )
        return pending or _pairing(plan) != self._paired_masks

    def _masks_ready_item(self, review: AxisReview | None) -> tuple[bool, str] | None:
        """Return the "Before you run" item of the masks, or None without masks."""
        plan = self._planned_masks() if self._show_output else None
        if self._masks_stale(plan):
            return False, "Check the masks again with the folder and mask axes chosen (step 1)"
        if self.preflight is None or self.preflight.masks is None:
            return None
        return not has_errors(self._mask_issues_now(review)), "Fix any mask errors (step 1, Masks)"

    def _refresh_masks(self, review: AxisReview | None) -> None:
        """Show the recorded masks folder, the masks found, their axes and their messages."""
        plan = self._planned_masks() if self._show_output else None
        self._show_recorded_masks(plan)
        masks = None if self.preflight is None else self.preflight.masks
        ui.set_visible(self.masks_card, self._show_output and masks is not None)
        if masks is None or not self._show_output:
            self._mask_choices, self._mask_questions = {}, None
            self.mask_axes_box.children = []
            return
        issues = self._mask_issues_now(review)
        self.masks_table.value = _types_html(masks, self.preflight.metadata.files)
        self._show_mask_questions(mask_axes_questions(masks, issues, plan.mask_axes))
        self.masks_counts.value = issue_count_chips(issues)
        self.masks_issues.value = issues_html(issues) if issues else ""
        self.masks_status.value = _masks_status(masks, issues, self._masks_stale(plan))

    def _plan_masks_line(self) -> str:
        """Describe the masks the run records, for "What will happen" (HTML, or "")."""
        plan = self._planned_masks() if self._show_output else None
        if plan is not None and plan.folder is None and plan.recorded_folder is not None:
            return (
                "Masks: the masks folder of the earlier run is dropped; the manifest records "
                "no masks folder."
            )
        masks = None if self.preflight is None else self.preflight.masks
        if masks is None or not masks.types:
            return ""
        names = html.escape(", ".join(masks.types))
        reused = plan is not None and plan.recorded_folder is not None
        reused = reused and plan.folder == plan.recorded_folder
        return (
            f"Masks: <b>{plural(len(masks.types), 'mask type')}</b> ({names}) for "
            f"{plural(len(masks.images), 'image')}, paired with their images and recorded in "
            f"<b>run_manifest.json</b> with their axes"
            + (", from the masks folder of the earlier run" if reused else "")
            + ". No mask pixel is read."
        )

    def _show_recorded_masks(self, plan: PlannedMasks | None) -> None:
        """Say which masks folder the earlier run recorded, and offer to drop it."""
        recorded = None if plan is None else plan.recorded_folder
        if recorded is None:
            self._masks_dropped = False  # nothing recorded (any more) to drop
        typed = bool(ui.clean_path(self.masks_folder.value))
        ui.set_visible(self.masks_drop, recorded is not None and not typed)
        if recorded is None or typed:
            self.masks_recorded.value = ""
            return
        where = f"<b>{html.escape(recorded.as_posix())}</b>"
        if self._masks_dropped:
            self.masks_drop.description = " Undo: keep the recorded masks"
            self.masks_recorded.value = ui.status(
                "warn",
                "The masks of the earlier run are dropped.",
                [f"The run records no masks folder instead of {where}."],
                raw=True,
            )
            return
        self.masks_drop.description = " Drop the recorded masks"
        self.masks_recorded.value = ui.status(
            "info",
            "The earlier run in the output folder used a masks folder.",
            [
                f"With this box empty, the run uses it again: {where}.",
                "To run without masks, click <b>Drop the recorded masks</b>.",
            ],
            raw=True,
        )

    def _show_mask_questions(self, questions: list[MaskAxesQuestion]) -> None:
        """Show one statement per mask type that needs (or has) one; keep pending choices."""
        if tuple(questions) == self._mask_questions:
            return  # the same questions: keep the dropdowns (one may be in use)
        self._mask_questions = tuple(questions)
        earlier = {name: choice.value for name, choice in self._mask_choices.items()}
        self._mask_choices, rows = {}, []
        for question in questions:
            options = [("Not stated: choose the axes", _NOT_STATED)] + [
                (_choice_text(roles), roles) for roles in question.choices
            ]
            value = earlier.get(question.mask_type) or question.stated or _NOT_STATED
            if value not in {roles for _, roles in options}:
                value = _NOT_STATED
            choice = widgets.Dropdown(options=options, value=value, layout=widgets.Layout(**_WIDE))
            choice.observe(lambda _: self._refresh_run_step(), names="value")
            self._mask_choices[question.mask_type] = choice
            rows.append(widgets.HTML(_question_html(question)))
            rows.append(choice if question.choices else widgets.HTML(""))
        if rows:
            rows.insert(0, widgets.HTML(_QUESTIONS_INTRO))
        self.mask_axes_box.children = rows


def _pairing(plan: PlannedMasks) -> tuple:
    """Return what a pairing of the masks depends on, to tell when it is out of date."""
    folder = None if plan.folder is None else plan.folder.as_posix()
    return folder, tuple(sorted(plan.mask_axes.items()))


_QUESTIONS_INTRO = (
    "<b>Mask axes</b><div class='mb-help'>Some masks need their axes stated, once per mask "
    "type, as the command line's <b>--mask-axes TYPE=ROLES</b>: one role per axis, in the "
    "order the files store them. Nothing is guessed; choose, then click <b>Check the "
    "masks</b>.</div>"
)


def _question_html(question: MaskAxesQuestion) -> str:
    """Say why a mask type's axes are asked for, and what its files store."""
    name = f"<b>{html.escape(question.mask_type)}</b>"
    stored = ", ".join(html.escape(axes) for axes in question.header_axes) or "unknown"
    if not question.choices:
        why = (
            "no statement fits every mask of this type (their files store different axes, or "
            "an axis no mask can have). Save the masks of the type alike, as 2D label images "
            "or with their axes named."
        )
    else:
        why = "their files do not name what their axes are."
    return f"<div class='mb-help'>{name} (axes in the files: {stored}): {why}</div>"


def _choice_text(roles: str) -> str:
    """Describe a statement, e.g. ``"TYX: time (T), height (Y), width (X)"``."""
    return f"{roles}: " + ", ".join(_ROLE_WORDS[role] for role in roles)


def _types_html(masks: MaskInventory, images: Sequence[TiffMetadata]) -> str:
    """Return the table of the mask types and the images without a mask of each."""
    rows = [("Mask type", "From", "Axes", "Masks", "Images without one")]
    lists = []
    for row in mask_type_rows(masks, [f.relative_path for f in images]):
        axes = ", ".join(row.axes) or "unknown"
        if row.axes and row.stated:
            axes += " (stated)"
        sources = " and ".join("label images" if s == "ome-zarr" else "folder" for s in row.sources)
        rows.append((row.mask_type, sources, axes, f"{row.masks:,}", f"{len(row.without):,}"))
        if row.without:
            lists.append(
                f"<div class='mb-help'>Without a <b>{html.escape(row.mask_type)}</b> mask:</div>"
                + ui.path_list(list(row.without), limit=5)
            )
    if len(rows) == 1:
        return ""
    return ui.table(rows, numeric_from=3) + "".join(lists)


def _masks_status(masks: MaskInventory, issues: list[ValidationIssue], stale: bool) -> str:
    """Return the masks card's status: out of date, errors, or what was found."""
    if stale:
        return ui.status(
            "info",
            "The masks folder or the mask axes changed.",
            ["Click Check the masks to pair the masks again."],
        )
    if not masks.types and not issues:
        return ui.status("info", "No masks were found in the masks folder.")
    found = f"{plural(len(masks.types), 'mask type')} for {plural(len(masks.images), 'image')}"
    if has_errors(issues):
        return ui.status(
            "error",
            f"{found}: some masks cannot be used.",
            ["Read the messages below; each one says what to do.", "Then check the masks again."],
        )
    if issues:
        return ui.status("warn", f"{found}, with warnings to read.")
    return ui.status("ok", f"{found}; every mask fits its image.")
