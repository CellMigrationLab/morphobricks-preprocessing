"""The Cellpose segmentation as one widget (``segmentation_app``), for any notebook cell.

The combined notebook (#441) mounts it in a cell after the Preprocessing app's: the same forms
as the Cellpose notebook's cells, with the run taken from the folders given or from the app.
"""

from types import SimpleNamespace

import ipywidgets as widgets
import pytest

from cellpose_stub import install
from morphobricks_preprocessing.notebook_cellpose_app import (
    STEPS,
    SegmentationApp,
    app_folders,
    segmentation_app,
)
from morphobricks_preprocessing.notebook_cellpose_form import CellposeNotebook
from morphobricks_preprocessing.segmentation_record import read_record


def walk(widget):
    yield widget
    for child in getattr(widget, "children", ()):
        yield from walk(child)


@pytest.fixture
def stub(monkeypatch, tmp_path):
    return install(monkeypatch, tmp_path)


def segment(app, cell="TUB", nuclear="DNA"):
    workflow = app.workflow
    workflow.forms["channels"].button.click()
    workflow.picker.cell.value, workflow.picker.nuclear.value = cell, nuclear
    workflow.forms["model"].fields["device"].value = "cpu"
    for step in ("model", "segment"):
        form = workflow.forms[step]
        form.button.click()
        assert form.refusal == "", form.refusal


def test_given_the_folders_it_opens_the_run_and_holds_every_step(mixed_run, stub):
    app = segmentation_app(mixed_run.out, mixed_run.masks)
    assert isinstance(app, SegmentationApp) and isinstance(app.workflow, CellposeNotebook)
    assert list(app.workflow.forms) == list(STEPS)
    assert list(app.children) == [app.stepper.widget]  # one step on screen at a time
    pages = [set(map(id, walk(page))) for page in app.stepper.pages]
    for name in STEPS:  # every form's controls are on one page of the stepper
        assert sum(id(app.workflow.forms[name].body) in page for page in pages) == 1
    assert app.workflow.inputs is not None and "Run opened" in app.workflow.shown[-1]


def test_it_takes_an_opened_run_and_reports_the_masks_written(mixed_run, stub):
    batches = []
    app = segmentation_app(mixed_run.inputs, mixed_run.masks, on_masks_written=batches.append)
    segment(app)
    (result,) = batches
    assert result.counts()["written"] == len(mixed_run.inputs.fovs())
    assert result.record is not None and read_record(mixed_run.masks, "Cells").batches


def test_without_folders_the_boxes_are_blank_and_nothing_opens():
    app = segmentation_app()
    fields = app.workflow.forms["open"].fields
    assert (fields["run_folder"].value, fields["masks_folder"].value) == ("", "")
    assert app.workflow.inputs is None and app.workflow.shown == []


def test_the_folders_come_from_a_preprocessing_apps_finished_run(mixed_run, stub):
    done = SimpleNamespace(result=SimpleNamespace(written=True, output_folder=mixed_run.out))
    # The app's own rule (#441): the masks folder it checked, else the one its run recorded;
    # never what its masks box says now.
    done.masks_folder = widgets.Text(value="/typed/after/the/run")
    done._runs_masks_folder = lambda: mixed_run.masks
    assert app_folders(done) == (str(mixed_run.out), str(mixed_run.masks))
    assert segmentation_app(app=done).workflow.inputs is not None
    unfinished = SimpleNamespace(result=SimpleNamespace(written=False, output_folder="x"))
    assert app_folders(unfinished) == ("", "")  # no run is guessed
    assert app_folders(SimpleNamespace(result=None)) == ("", "")


def test_a_plain_cell_mounts_it_in_a_real_kernel(mixed_run):
    nbformat = pytest.importorskip("nbformat")
    pytest.importorskip("ipykernel", reason="the dev extra installs ipykernel")
    from test_notebook_file import _execute

    cell = nbformat.v4.new_code_cell(
        "from morphobricks_preprocessing.notebook_cellpose_app import segmentation_app\n"
        f"app = segmentation_app({str(mixed_run.out)!r}, {str(mixed_run.masks)!r})\n"
        "print('OPENED', app.workflow.inputs is not None)\n"
        "app\n"
    )
    notebook = nbformat.v4.new_notebook(cells=[cell])
    _execute(notebook)
    outputs = notebook.cells[0].outputs
    assert [o for o in outputs if o.output_type == "error"] == []
    assert "OPENED True" in "".join(o.get("text", "") for o in outputs)
    assert any("application/vnd.jupyter.widget-view+json" in o.get("data", {}) for o in outputs)
