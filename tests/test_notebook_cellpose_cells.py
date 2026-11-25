"""The Cellpose notebook's cells after the structure audit (owner, 23 November 2026).

``CellStop`` is defined once, in :mod:`morphobricks_preprocessing.notebook_stop`; cells 2 to 8
import it and only cell 1 (the install, run before the package is there) keeps its own copy.
"""

import json
import sys

import pytest

from cellpose_stub import install
from morphobricks_preprocessing import notebook_cellpose, notebook_stop
from morphobricks_preprocessing import segmentation_cellpose as cp
from test_notebook_cellpose import CHANNELS, DRIVE, INSTALL, LAST, MODEL, NOTEBOOK, OPEN, source

IMPORT = "from morphobricks_preprocessing.notebook_stop import CellStop"


def test_cellstop_is_defined_once_in_the_package_and_cell_1_keeps_its_copy():
    assert "class CellStop(RuntimeError):" in source(INSTALL)
    for number in range(DRIVE, LAST + 1):
        text = source(number)
        assert "class CellStop" not in text, f"cell {number + 1}"
        assert "Run cell 1 first: it installs what this notebook needs." in text
        if number != OPEN:  # cell 3 stops on nothing of its own
            assert IMPORT in text, f"cell {number + 1}"
    assert notebook_cellpose.CellStop is notebook_stop.CellStop
    assert notebook_stop.CellStop("one line")._render_traceback_() == ["one line"]


def test_a_cell_run_too_early_stops_with_one_line_in_a_real_kernel():
    nbformat = pytest.importorskip("nbformat")
    pytest.importorskip("ipykernel", reason="the dev extra installs ipykernel")
    from test_notebook_file import _execute

    notebook = nbformat.v4.new_notebook(cells=[nbformat.v4.new_code_cell(source(CHANNELS))])
    _execute(notebook, allow_errors=True)
    (error,) = [o for o in notebook.cells[0].outputs if o.output_type == "error"]
    assert (error.ename, error.evalue) == ("CellStop", "Run cell 3 first.")
    assert error.traceback == ["Run cell 3 first."]


# the device status, before loading ----------------------------------------------------------


def _model_form(cells):
    from test_notebook_cellpose import open_and_choose

    open_and_choose(cells)
    exec(compile(source(MODEL), "<cell 5>", "exec"), cells.ns)
    return cells.ns["workflow"].forms["model"]


@pytest.fixture
def cells(mixed_run, monkeypatch, tmp_path):
    state = install(monkeypatch, tmp_path)
    return type("C", (), {"ns": {}, "stub": state, "run": mixed_run})()


def _status(form):
    return form.body.children[0].value  # the device line, the first thing of the step


def test_the_model_form_says_no_gpu_was_found_before_anything_loads(cells):
    form = _model_form(cells)
    status = _status(form)
    assert "mb-warn" in status and "No GPU found here" in status
    assert form.fields["device"].value == "cuda"  # it says so; it chooses nothing
    assert cells.stub.constructed == []


def test_the_model_form_names_the_gpu_it_found(cells, monkeypatch):
    cells.stub.cuda = True
    torch = sys.modules["torch"]
    monkeypatch.setattr(torch.cuda, "get_device_name", lambda index: "Tesla T4", raising=False)
    monkeypatch.setattr(torch.cuda, "device_count", lambda: 1, raising=False)
    status = _status(_model_form(cells))
    assert "mb-ok" in status and "GPU found: cuda (Tesla T4; 1 device(s))" in status
    cells.stub.cuda, cells.stub.mps = False, True
    assert "GPU found: mps" in _status(_model_form(cells))
    assert cells.stub.constructed == []


def test_without_pytorch_the_status_says_so_and_nothing_stops(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", None)  # an import of it fails
    found, line = cp.device_status()
    assert not found and line.startswith("PyTorch is not installed")


# versions and citation -------------------------------------------------------------------------


def test_the_notebook_ends_with_the_versions_and_the_citation():
    cells = json.loads(NOTEBOOK.read_text(encoding="utf-8"))["cells"]
    last = cells[-1]
    text = "".join(last["source"])
    assert last["cell_type"] == "markdown" and "## **Versions and citation**" in text
    assert f"Cellpose {cp.CELLPOSE_VERSION}" in text
    for needle in (
        "segmentation_record.json",
        "`tool_version`",
        "`cellpose_version`",
        "SHA-256",
        "`dinov3_commit`",
        # As Cellpose 4.2.1.1's README asks (read from its sdist, 23 November 2026).
        "Pachitariu, M., Rariden, M., & Stringer, C. (2025). Cellpose-SAM",
        "Stringer, C., Wang, T., Michaelos, M., & Pachitariu, M. (2021). Cellpose:",
        "Pachitariu, M. & Stringer, C. (2022). Cellpose 2.0",
    ):
        assert needle in text, needle
