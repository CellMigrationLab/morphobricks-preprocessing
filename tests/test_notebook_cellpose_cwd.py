"""A relative masks folder is the folder it named when the run was opened, never another.

External review aq (24 November 2026), B1: the masks folder was kept as typed, so a relative
path such as ``masks`` followed a later change of the kernel's working directory (``%cd``,
``os.chdir``): Preview and Segment passed the stale-folder check (the same text) and the masks
and the record were written under the new directory. The folder is now fixed, resolved, when
the run is opened; a press after the working directory changed is refused (``RUN_CHANGED``)
and nothing is written anywhere. A stand-in Cellpose (``cellpose_stub``) on a run whose images
all have DNA and TUB.
"""

import pytest

from cellpose_stub import install, two_channel_run
from morphobricks_preprocessing.notebook_cellpose_app import segmentation_app
from morphobricks_preprocessing.notebook_cellpose_guard import RUN_CHANGED
from morphobricks_preprocessing.segmentation_record import read_record
from test_notebook_cellpose import BATCH, CHANNELS, MODEL, OPEN, PREVIEW, run_cell, source

OUTPUTS = {"cells": ("Cells",), "both": ("Cells", "Nuclei")}


@pytest.fixture
def setup(monkeypatch, tmp_path):
    """The run (absolute path), the stand-in Cellpose, and two working directories A and B."""
    stub = install(monkeypatch, tmp_path)
    run = two_channel_run(tmp_path / "run")
    a, b = tmp_path / "A", tmp_path / "B"
    a.mkdir()
    b.mkdir()
    monkeypatch.chdir(a)
    return type("S", (), {"stub": stub, "run": run, "a": a, "b": b})()


def press(form):
    form.button.click()
    return form.refusal


def written_anywhere(setup):
    """Return every file under A and B (the masks folder opened, and where ``masks`` now is)."""
    return sorted(str(p) for folder in (setup.a, setup.b) for p in folder.rglob("*"))


def choose_and_load(workflow, output):
    channels = workflow.forms["channels"]
    channels.fields["output"].value = output
    assert press(channels) == ""
    workflow.picker.cell.value, workflow.picker.nuclear.value = "TUB", "DNA"
    workflow.forms["model"].fields["device"].value = "cpu"
    assert press(workflow.forms["model"]) == ""


def refused_after_chdir(setup, workflow, monkeypatch):
    """Change the working directory to B, press Preview and Segment: both refused."""
    monkeypatch.chdir(setup.b)  # the form still says "masks"
    refusals = [press(workflow.forms[step]) for step in ("preview", "segment")]
    assert written_anywhere(setup) == []
    assert not (setup.b / "masks").exists()
    assert refusals == [RUN_CHANGED, RUN_CHANGED]
    assert setup.stub.calls == [] and workflow.results == {}
    assert workflow.masks_root == (setup.a / "masks").resolve()  # fixed when opened


@pytest.mark.parametrize("output", sorted(OUTPUTS))
def test_the_notebook_cells_refuse_after_the_working_directory_changed(setup, monkeypatch, output):
    ns = {}
    run_cell(ns, OPEN, run_folder=str(setup.run.out), masks_folder="masks")
    run_cell(ns, CHANNELS, output=output)
    ns["workflow"].picker.cell.value, ns["workflow"].picker.nuclear.value = "TUB", "DNA"
    run_cell(ns, MODEL, device="cpu")
    for cell in (PREVIEW, BATCH):
        exec(compile(source(cell), f"<cell {cell + 1}>", "exec"), ns)
    workflow = ns["workflow"]
    refused_after_chdir(setup, workflow, monkeypatch)
    monkeypatch.chdir(setup.a)  # back where it was opened: the folder opened is used
    assert press(workflow.forms["segment"]) == ""
    for mask_type in OUTPUTS[output]:
        assert read_record(setup.a / "masks", mask_type).batches
    assert not (setup.b / "masks").exists()


@pytest.mark.parametrize("output", sorted(OUTPUTS))
def test_segmentation_app_refuses_after_the_working_directory_changed(setup, monkeypatch, output):
    app = segmentation_app(setup.run.out, "masks")
    workflow = app.workflow
    assert workflow.inputs is not None
    choose_and_load(workflow, output)
    refused_after_chdir(setup, workflow, monkeypatch)


def test_a_relative_run_folder_is_refused_after_the_working_directory_changed(setup, monkeypatch):
    """The run folder was already compared resolved (RunInputs holds it resolved); kept so."""
    relative = f"../{setup.run.out.relative_to(setup.a.parent).as_posix()}"
    app = segmentation_app(relative, str(setup.a / "masks"))
    workflow = app.workflow
    assert workflow.inputs is not None
    choose_and_load(workflow, "cells")
    monkeypatch.chdir(setup.b / ".." / "..")
    for step in ("preview", "segment"):
        assert press(workflow.forms[step]) == RUN_CHANGED
    assert setup.stub.calls == [] and written_anywhere(setup) == []
