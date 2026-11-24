"""What keeps the Cellpose notebook's buttons from acting on folders or a workflow no longer shown.

Two guards, split from :mod:`.notebook_cellpose_form` to keep each file small:

- :func:`check_folders`: the run folder and the masks folder that cell 3's form shows must be
  those the run was opened with; otherwise Preview and Segment are refused (:data:`RUN_CHANGED`),
  as a changed model is (``MODEL_CHANGED``). Nothing is written to a folder the form no longer
  shows.
- :func:`take_over`: running cell 3 again makes a new workflow, while the forms of cells 4 to 8
  already on screen still belong to the old one. The kernel keeps one current workflow; a new one
  retires the one before (:func:`retire`): every field and button of its forms is disabled and
  each form says why (:data:`REPLACED`). A press on a retired form is refused.
"""

from __future__ import annotations

import weakref
from pathlib import Path
from typing import TYPE_CHECKING

from . import notebook_style as ui
from .notebook_stop import CellStop

if TYPE_CHECKING:
    from .notebook_cellpose_form import CellposeNotebook

RUN_CHANGED = (
    "The run folder or the masks folder of the Open the run step changed since the run was "
    "opened: press Open the run again. Nothing is written to a folder the form no longer shows."
)
"""The refusal of a preview or a run whose cell 3 folders no longer match the run opened."""
REPLACED = (
    "Replaced: this segmentation step was run again, so this form no longer works. Use the "
    "forms shown after it."
)
"""What a retired form says, and the refusal of a press on it."""
_current: weakref.ReferenceType[CellposeNotebook] | None = None


def check_folders(workflow: CellposeNotebook) -> None:
    """Refuse when cell 3's form shows other folders than those the run was opened with.

    The fields are tidied as **Open the run** tidies them (``clean_path``) and both are compared
    resolved now, with the folders fixed when the run was opened (:class:`.RunInputs` holds the
    run folder resolved, the workflow the masks folder): a relative path that the kernel's
    working directory, changed since, sends elsewhere is refused (review aq, B1).

    Raises
    ------
    CellStop
        With :data:`RUN_CHANGED`.
    """
    form = workflow.forms.get("open")
    if form is None or workflow.inputs is None:
        return
    run = ui.clean_path(form.fields["run_folder"].value)
    masks = ui.clean_path(form.fields["masks_folder"].value)
    same_run = bool(run) and Path(run).resolve() == workflow.inputs.output_folder
    same_masks = bool(masks) and Path(masks).expanduser().resolve() == workflow.masks_root
    if not (same_run and same_masks):
        raise CellStop(RUN_CHANGED)


def check_current(workflow: CellposeNotebook) -> None:
    """Refuse a press on the forms of a retired workflow.

    Raises
    ------
    CellStop
        With :data:`REPLACED`.
    """
    if workflow.replaced:
        raise CellStop(REPLACED)


def take_over(workflow: CellposeNotebook) -> None:
    """Make ``workflow`` the kernel's current one and retire the one before it, if any."""
    global _current
    previous = _current() if _current is not None else None
    if previous is not None and previous is not workflow:
        retire(previous)
    _current = weakref.ref(workflow)


def retire(workflow: CellposeNotebook) -> None:
    """Disable every field and button of ``workflow``'s forms; each form says it was replaced."""
    workflow.replaced = True
    widgets = [w for form in workflow.forms.values() for w in _walk(form.widget)]
    if workflow.picker is not None:
        picker = workflow.picker
        widgets.extend(
            [*picker.dropdowns, *getattr(picker, "number_fields", ()), getattr(picker, "by", None)]
        )
    for widget in widgets:
        if widget is not None and widget.has_trait("disabled"):
            widget.disabled = True
    for form in workflow.forms.values():
        form.notice.value = ui.status("warn", REPLACED)


def _walk(widget: object) -> list:
    """Return ``widget`` and every widget inside it."""
    return [widget, *(w for child in getattr(widget, "children", ()) for w in _walk(child))]
