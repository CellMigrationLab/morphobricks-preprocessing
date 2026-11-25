"""The Cellpose segmentation as one widget, to mount in any notebook cell.

:func:`segmentation_app` returns the segmentation as four steps, one on screen at a time, with
the Preprocessing app's stepper (:class:`.notebook_stepper.Stepper`: a strip of chips, Back and
Next; owner, 25 November 2026): **Folders** (the run and the masks folder, checked there, and
the run's images), **Output and channels**, **Model and preview** (Preview loads the model the
first time, then reuses it) and **Segment**. Moving between steps keeps every answer; nothing
is recorded on a dropdown change. It is the same :class:`.CellposeNotebook` and the same forms
that the Cellpose notebook's cells mount one per cell: there is no second implementation.

For a notebook that runs preprocessing first (the combined notebook, #441), a cell after the
Preprocessing app's cell can call ``segmentation_app(app=app)``: the run's output folder is
taken from the app's finished run (``app.result.output_folder`` when ``app.result.written``)
and the masks folder by the app's own rule (``app._runs_masks_folder()``: the masks folder
checked, else the one the run recorded; never the raw text of its masks box), read as they are
when the cell runs. Nothing of the Preprocessing app is imported here.
"""

from __future__ import annotations

import os
from collections.abc import Callable

import ipywidgets as widgets

from . import notebook_style as ui
from . import segmentation_cellpose as cp
from .notebook_cellpose_form import CellposeNotebook
from .notebook_stepper import Step, Stepper, StepState
from .run_inputs import RunInputs

STEPS = ("open", "channels", "model", "preview", "segment")
"""The forms of :func:`segmentation_app`, in order (one per cell in the Cellpose notebook)."""
PAGES = (
    ("Folders", "Open the run and choose the masks folder; look at its images.", ("open",)),
    ("Output and channels", "What to segment, and the channels by ID.", ("channels",)),
    (
        "Model and preview",
        "Choose the model; Preview loads it once and segments one image.",
        ("model", "preview"),
    ),
    ("Segment", "Segment every image and write the masks.", ("segment",)),
)
"""The stepper's pages: chip title, one-line purpose and the forms each shows."""
WAITING = {
    1: ("run", "Open the run in step 1 (Folders) first."),
    2: ("channels", "Choose the channels in step 2 (Output and channels) first."),
    3: ("channels", "Choose the channels in step 2 (Output and channels) first."),
}
"""Why a page cannot be answered yet, by page: what it needs and the line it shows."""


class SegmentationApp(widgets.VBox):
    """The steps of :data:`PAGES`, one shown at a time; :attr:`workflow` is the state behind.

    Attributes
    ----------
    workflow
        The :class:`.CellposeNotebook` behind the forms.
    stepper
        The :class:`.notebook_stepper.Stepper` (``go(index)`` opens a page).
    """

    def __init__(self, workflow: CellposeNotebook) -> None:
        """Hold ``workflow`` and show its forms in a stepper."""
        self.workflow = workflow
        self.stepper = Stepper([_page(workflow, *page) for page in PAGES])
        workflow.on_change.append(self.stepper.refresh)
        super().__init__([self.stepper.widget], layout=widgets.Layout(width="99%"))


def _page(workflow: CellposeNotebook, title: str, purpose: str, names: tuple[str, ...]) -> Step:
    """Return one page of the stepper: the bodies of the forms ``names``, and its state."""
    index = [page[0] for page in PAGES].index(title)
    forms = [workflow.forms[name] for name in names]
    if "model" in names:  # Preview loads the model: no button of its own here
        ui.set_visible(workflow.forms["model"].button, False)
    children = [form.body for form in forms]

    def waiting() -> str:
        need = WAITING.get(index)
        missing = need is not None and (
            workflow.inputs is None if need[0] == "run" else workflow.picker is None
        )
        return ui.status("info", need[1]) if missing else ""

    return Step(title, purpose, children, lambda: _state(workflow, index), waiting)


def _state(workflow: CellposeNotebook, index: int) -> StepState:
    """Return what page ``index`` says about itself, from the workflow's state only."""
    if index == 0:
        if workflow.inputs is None:
            return StepState(False, "No run opened yet.")
        return StepState(True, f"{len(workflow.inputs.fovs())} images; masks to "
                         f"{workflow.masks_root}")  # fmt: skip
    if index == 1:
        if workflow.picker is None or not workflow.picker.targets:
            return StepState(False, "No channels chosen yet.")
        return StepState(True, ", ".join(workflow.mask_types.values()))
    if index == 2:
        if workflow.loaded is None:
            return StepState(False, "No model loaded yet.")
        if not workflow.model_is_current():
            return StepState(False, "The model changed: Preview loads it.")
        return StepState(True, f"{workflow.loaded.name} on {workflow.loaded.device}")
    if not workflow.results:
        return StepState(False, "Nothing segmented yet.")
    written = sum(r.counts()["written"] for r in workflow.results.values())
    return StepState(True, f"{written} masks written")


def app_folders(app: object) -> tuple[str, str]:
    """Return the run folder and the masks folder a Preprocessing app holds, or blanks.

    Both are blank unless the app's last run was written, or, before any run in this session,
    the app restored a finished run of its output folder (``app.restored_run``,
    :mod:`.notebook_restore`; the segmentation opens it through ``RunInputs``, which checks it
    again). The run folder is that run's output folder; the masks folder is the app's own rule,
    ``_runs_masks_folder()`` (the masks folder the app checked, else the one the run recorded;
    blank for none), never what its masks box says now. Nothing is guessed.
    """
    result = getattr(app, "result", None)
    if result is None and getattr(app, "restored_run", None) is not None:
        folder = app.restored_run
    elif result is None or not getattr(result, "written", False):
        return "", ""
    else:
        folder = result.output_folder
    masks = app._runs_masks_folder()
    return os.fspath(folder), "" if masks is None else os.fspath(masks)


def segmentation_app(
    run_folder: str | os.PathLike | RunInputs | None = None,
    masks_folder: str | os.PathLike | None = None,
    *,
    app: object = None,
    on_masks_written: Callable[[cp.BatchResult], object] | None = None,
    register_with: str | None = None,
) -> SegmentationApp:
    """Return the whole Cellpose segmentation as one widget.

    Parameters
    ----------
    run_folder
        The preprocessing run's output folder, or the run opened (:class:`.RunInputs`). None
        takes it from ``app``, or leaves the box blank for the user.
    masks_folder
        The masks folder; None takes it from ``app``, or leaves it blank.
    app
        A Preprocessing app (``PreprocessingNotebook``) whose finished run and masks box give
        the folders not given (:func:`app_folders`).
    on_masks_written
        Called with the :class:`.segmentation_cellpose.BatchResult` after each batch (its
        ``record`` is the record file, ``counts()`` what was written), e.g. to rescan the masks.
    register_with
        Where the notebook registers the masks itself (the combined notebook's cell 5); the
        line shown once the masks are written then names it
        (:func:`.segmentation_cellpose_run.next_step_line`). None: the declaration by hand.

    Returns
    -------
    SegmentationApp
        The steps of :data:`PAGES` in a stepper; ``.workflow`` is the
        :class:`.CellposeNotebook` behind them. Given both folders, the run is opened at once
        (its result, or its refusal, is shown under **Open the run**).
    """
    found = app_folders(app) if app is not None else ("", "")
    if isinstance(run_folder, RunInputs):
        run_folder = run_folder.output_folder
    run = os.fspath(run_folder) if run_folder is not None else found[0]
    masks = os.fspath(masks_folder) if masks_folder is not None else found[1]
    workflow = CellposeNotebook()
    workflow.on_masks_written = on_masks_written
    workflow.register_with = register_with
    workflow.load_on_preview = True
    for step in STEPS:
        getattr(workflow, f"{step}_form")()
    segmentation = SegmentationApp(workflow)
    opener = workflow.forms["open"]
    opener.fields["run_folder"].value, opener.fields["masks_folder"].value = run, masks
    if run and masks:
        opener.submit()
    return segmentation
