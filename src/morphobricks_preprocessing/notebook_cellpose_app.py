"""The Cellpose segmentation as one widget, to mount in any notebook cell.

:func:`segmentation_app` returns every step of the segmentation (the run, the output and the
channels, the model and settings, the example result, segmenting the whole experiment, what
next) as one widget. It is the same :class:`.CellposeNotebook` and the same forms that the
Cellpose notebook's cells mount one per cell: there is no second implementation.

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

from . import segmentation_cellpose as cp
from .notebook_cellpose_form import CellposeNotebook
from .run_inputs import RunInputs

STEPS = ("open", "channels", "model", "preview", "segment", "next")
"""The forms of :func:`segmentation_app`, in order (one per cell in the Cellpose notebook)."""


class SegmentationApp(widgets.VBox):
    """The forms of :data:`STEPS`, one under the other; :attr:`workflow` is the state behind."""

    def __init__(self, workflow: CellposeNotebook, children: list[widgets.Widget]) -> None:
        """Hold ``workflow`` and show ``children``."""
        super().__init__(children, layout=widgets.Layout(width="99%"))
        self.workflow = workflow


def app_folders(app: object) -> tuple[str, str]:
    """Return the run folder and the masks folder a Preprocessing app holds, or blanks.

    Both are blank unless the app's last run was written. The run folder is that run's output
    folder; the masks folder is the app's own rule, ``_runs_masks_folder()`` (the masks folder
    the app checked, else the one the run recorded; blank for none), never what its masks box
    says now. Nothing is guessed.
    """
    result = getattr(app, "result", None)
    if result is None or not getattr(result, "written", False):
        return "", ""
    masks = app._runs_masks_folder()
    return os.fspath(result.output_folder), "" if masks is None else os.fspath(masks)


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
        Where the notebook registers the masks itself (the combined notebook's cell 5); its
        **What to do next** then names it (:func:`.segmentation_cellpose_run.next_steps_lines`).
        None: the steps by hand, as in the Cellpose notebook.

    Returns
    -------
    SegmentationApp
        The forms of :data:`STEPS`, one under the other; ``.workflow`` is the
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
    forms = [getattr(workflow, f"{step}_form")() for step in STEPS]
    opener = workflow.forms["open"]
    opener.fields["run_folder"].value, opener.fields["masks_folder"].value = run, masks
    if run and masks:
        opener.submit()
    return SegmentationApp(workflow, [form.widget for form in forms])
