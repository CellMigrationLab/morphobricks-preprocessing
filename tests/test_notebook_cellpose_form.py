"""The Cellpose notebook's forms: every setting a widget, in Colab, JupyterLab and LabConstrictor.

The cells once took their settings through Colab form fields (``# @param``), which are plain
comments elsewhere. Each step's cell now shows a form (``notebook_cellpose_form``); these tests
check that every field reaches the workflow as given, that the safety defaults and refusals are
unchanged, and that the cells run in a plain Jupyter kernel.
"""

import html
import json
from pathlib import Path

import ipywidgets as widgets
import pytest

from cellpose_stub import install
from morphobricks_preprocessing import notebook_cellpose
from morphobricks_preprocessing import notebook_cellpose_form as form_module
from morphobricks_preprocessing import segmentation_cellpose as cp
from morphobricks_preprocessing.notebook_cellpose_form import STOPPED, CellposeNotebook
from morphobricks_preprocessing.segmentation_record import DEVICES, read_record
from test_notebook_cellpose import (
    BATCH,
    CHANNELS,
    DRIVE,
    MODEL,
    NOTEBOOK,
    OPEN,
    open_and_choose,
    run_cell,
    source,
)

ROOT = Path(__file__).parents[1]
NOTEBOOKS = sorted((ROOT / "notebooks").glob("*.ipynb"))


@pytest.fixture
def cells(mixed_run, monkeypatch, tmp_path):
    state = install(monkeypatch, tmp_path)
    return type("C", (), {"ns": {}, "stub": state, "run": mixed_run})()


def shown_forms():
    """Return a workflow with every step's form shown (nothing pressed)."""
    workflow = CellposeNotebook()
    for step in ("open", "channels", "model", "preview", "segment", "next"):
        getattr(workflow, f"{step}_step")()
    return workflow


def walk(widget):
    """Yield ``widget`` and every widget inside it."""
    yield widget
    for child in getattr(widget, "children", ()):
        yield from walk(child)


# no Colab form fields --------------------------------------------------------------------------


@pytest.mark.parametrize("path", NOTEBOOKS, ids=lambda p: p.stem)
def test_no_notebook_has_a_colab_form_field(path):
    """A ``# @param`` is a comment outside Colab: a user there would have to edit code."""
    assert len(NOTEBOOKS) == 2
    for cell in json.loads(path.read_text(encoding="utf-8"))["cells"]:
        text = "".join(cell["source"])
        assert "@param" not in text and "@markdown" not in text, text[:80]


# the forms ------------------------------------------------------------------------------------


def test_the_forms_start_with_the_former_defaults():
    forms = shown_forms().forms
    value = {name: f.value for form in forms.values() for name, f in form.fields.items()}
    assert value == {
        "run_folder": "",
        "masks_folder": "",
        "output": "cells",
        "mask_type": "Cells",
        "model": "cpsam_v2",
        "custom_model_file": "",
        "device": "cuda",  # never chosen for the user: an unavailable GPU is refused
        "download_weights": False,
        "install_dinov3": False,
        "diameter": "",
        "flow_threshold": 0.4,
        "cellprob_threshold": 0.0,
        "batch_size": 8,
        "preview_image": "",
        "overwrite": False,
    }
    assert set(forms["model"].fields["device"].options) == set(DEVICES)
    assert tuple(forms["model"].fields["model"].options) == cp.BUILTIN_MODELS


def test_every_field_reaches_the_workflow_as_given(monkeypatch):
    calls = []
    steps = ("open", "choose_channels", "set_settings", "load", "preview", "run", "next_steps")
    for name in steps:
        monkeypatch.setattr(
            CellposeNotebook, name, lambda self, *a, name=name: calls.append((name, a))
        )
    forms = shown_forms().forms
    values = {
        "run_folder": " /data/run ",  # passed as typed: the workflow's own checks apply
        "masks_folder": "/data/masks",
        "output": "nuclei",
        "mask_type": "Nuclear",
        "model": "cpdino",
        "custom_model_file": "/models/mine",
        "device": "mps",
        "download_weights": True,
        "install_dinov3": True,
        "diameter": "17.5",
        "flow_threshold": 0.7,
        "cellprob_threshold": -1.5,
        "batch_size": 3,
        "preview_image": "a/b.tif",
        "overwrite": True,
    }
    for form in forms.values():
        for name, field in form.fields.items():
            field.value = values[name]
        form.button.click()
        assert form.stopped is None and not form.button.disabled
    assert calls == [
        ("open", (" /data/run ", "/data/masks")),
        ("choose_channels", ("Nuclear", "nuclei")),
        ("set_settings", ("17.5", 0.7, -1.5, 3)),
        ("load", ("cpdino", "/models/mine", "mps", True, True)),
        ("preview", ("a/b.tif",)),
        ("run", (True,)),
        ("next_steps", ()),
    ]


def test_the_settings_reach_cellpose_and_the_record(cells):
    open_and_choose(cells)
    run_cell(
        cells.ns,
        MODEL,
        device="cpu",
        model="cpsam",
        diameter="12",
        flow_threshold=0.6,
        cellprob_threshold=-2.0,
        batch_size=2,
    )
    run_cell(cells.ns, BATCH)
    (call, *_) = cells.stub.calls
    assert call.kwargs["flow_threshold"] == 0.6 and call.kwargs["cellprob_threshold"] == -2.0
    assert call.kwargs["batch_size"] == 2 and call.kwargs["diameter"] == 12.0
    assert call.kwargs["normalize"]["normalize"] is True  # always Cellpose's own
    batch = read_record(cells.run.masks, "Cells").batches[0]
    assert batch.parameters["batch_size"] == 2 and batch.parameters["diameter"] == 12.0
    assert batch.model_name == "cpsam" and batch.device == "cpu"


def test_a_refusal_is_shown_verbatim_in_a_red_card_and_the_form_can_be_pressed_again(
    cells, tmp_path
):
    missing = str(tmp_path / "nothing")
    with pytest.raises(notebook_cellpose.CellStop) as direct:
        CellposeNotebook().open(missing, str(tmp_path / "m"))
    with pytest.raises(notebook_cellpose.CellStop) as shown:
        run_cell(cells.ns, OPEN, run_folder=missing, masks_folder=str(tmp_path / "m"))
    assert str(shown.value) == str(direct.value)  # the same message as the cell stopped with
    workflow = cells.ns["workflow"]
    form = workflow.forms["open"]
    assert STOPPED in workflow.shown[-1]
    assert html.escape(str(direct.value)) in workflow.shown[-1]
    assert not form.button.disabled
    form.fields["run_folder"].value = str(cells.run.out)
    form.fields["masks_folder"].value = str(cells.run.masks)
    form.button.click()
    assert form.stopped is None and "Run opened" in workflow.shown[-1]


def test_nothing_downloads_without_the_tick(cells):
    cells.stub.models_dir.joinpath("cpsam_v2").unlink()
    open_and_choose(cells)
    with pytest.raises(notebook_cellpose.CellStop, match="Hugging Face"):
        run_cell(cells.ns, MODEL, device="cpu")  # the download box as the form starts: off
    assert cells.stub.downloaded == [] and cells.stub.constructed == []


def test_an_unavailable_gpu_is_refused_from_the_form_never_replaced_by_the_cpu(cells):
    open_and_choose(cells)
    for device in ("cuda", "mps"):
        with pytest.raises(notebook_cellpose.CellStop, match=f"{device} was asked for"):
            run_cell(cells.ns, MODEL, device=device)
    assert cells.stub.constructed == [] and cells.ns["workflow"].loaded is None


def test_dinov3_is_installed_only_on_its_tick_and_says_to_restart(cells, monkeypatch):
    installed = []
    monkeypatch.setattr(cp, "install_dinov3", lambda: installed.append(True))
    cells.stub.dinov3_commit = None
    open_and_choose(cells)
    with pytest.raises(notebook_cellpose.CellStop, match="restart the runtime"):
        run_cell(cells.ns, MODEL, device="cpu", model="cpdino", download_weights=True)
    assert installed == []
    with pytest.raises(notebook_cellpose.CellStop, match="restart the runtime"):
        run_cell(
            cells.ns,
            MODEL,
            device="cpu",
            model="cpdino",
            download_weights=True,
            install_dinov3=True,
        )
    assert installed == [True]
    assert "Installing DINOv3" in " ".join(cells.ns["workflow"].shown)


def test_overwrite_stays_off_unless_ticked(cells):
    open_and_choose(cells)
    run_cell(cells.ns, MODEL, device="cpu")
    run_cell(cells.ns, BATCH)
    run_cell(cells.ns, BATCH)  # the form as it starts: existing masks are skipped
    fovs = cells.run.inputs.fovs()
    assert cells.ns["workflow"].result.counts()["skipped"] == len(fovs)
    run_cell(cells.ns, BATCH, overwrite=True)
    assert cells.ns["workflow"].result.counts()["written"] == len(fovs)


@pytest.mark.parametrize("chosen", [None, "", "/picked/folder"])
def test_browse_fills_the_path_or_says_how_to_paste_it(monkeypatch, chosen):
    monkeypatch.setattr(form_module.ui, "ask_directory", lambda start: chosen)
    form = shown_forms().forms["open"]
    field = form.fields["run_folder"]
    field.value = "typed"
    browse = [w for w in walk(form.widget) if getattr(w, "description", "") == "Browse…"]
    browse[0].click()  # the run folder's
    assert field.value == (chosen or "typed")
    said = any("could not be opened" in getattr(w, "value", "") for w in walk(form.widget))
    assert said == (chosen is None)
    assert len(browse) == 2  # the masks folder has its own


def test_normalisation_is_not_a_choice_and_off_is_refused(cells):
    """Owner, 23 November 2026: images are never sent to Cellpose unnormalised."""
    assert "normalize" not in shown_forms().forms["model"].fields
    for value in (False, None, 0, "yes"):
        with pytest.raises(cp.SegmentationError, match="never sent to Cellpose unnormalised"):
            cp.Settings(normalize=value)
    settings = cp.Settings()
    object.__setattr__(settings, "normalize", False)  # past the check: still never reaches eval
    with pytest.raises(cp.SegmentationError, match="unnormalised"):
        cp.normalization(settings)
    open_and_choose(cells)
    run_cell(cells.ns, MODEL, device="cpu")
    run_cell(cells.ns, BATCH)
    params = read_record(cells.run.masks, "Cells").batches[0].parameters
    assert params["normalize"]["normalize"] is True
    assert params["normalize"]["percentile"] == [1.0, 99.0]
    assert all(call.kwargs["normalize"]["normalize"] is True for call in cells.stub.calls)


# a plain Jupyter kernel -----------------------------------------------------------------------


def test_the_cells_run_in_a_plain_jupyter_kernel(mixed_run):
    """Cells 2 to 4 in a real kernel (not Colab): the forms show, open the run and the channels."""
    nbformat = pytest.importorskip("nbformat")
    pytest.importorskip("ipykernel", reason="the dev extra installs ipykernel")
    from test_notebook_file import _execute

    press = nbformat.v4.new_code_cell(
        "form = workflow.forms['open']\n"
        f"form.fields['run_folder'].value = {str(mixed_run.out)!r}\n"
        f"form.fields['masks_folder'].value = {str(mixed_run.masks)!r}\n"
        "form.button.click()\n"
        "print('REFUSAL', repr(form.refusal))\n"
        "print('SHOWN', workflow.shown[-1])\n"
    )
    choose = nbformat.v4.new_code_cell(
        "form = workflow.forms['channels']\n"
        "form.button.click()\n"
        "print('REFUSAL', repr(form.refusal))\n"
        "print('CHANNELS', workflow.picker.channel_ids)\n"
    )
    code = [nbformat.v4.new_code_cell(source(n)) for n in (DRIVE, OPEN, CHANNELS)]
    notebook = nbformat.v4.new_notebook(cells=[code[0], code[1], press, code[2], choose])
    _execute(notebook)

    def text(cell):
        return "".join(output.get("text", "") for output in cell.outputs)

    errors = [o for c in notebook.cells for o in c.outputs if o.output_type == "error"]
    assert errors == []
    assert text(notebook.cells[0]).startswith("Not Google Colab")
    for cell in (notebook.cells[1], notebook.cells[3]):
        assert any(
            "application/vnd.jupyter.widget-view+json" in o.get("data", {}) for o in cell.outputs
        )
    assert "REFUSAL ''" in text(notebook.cells[2]) and "Run opened" in text(notebook.cells[2])
    assert "REFUSAL ''" in text(notebook.cells[4]) and "CHANNELS (" in text(notebook.cells[4])
    assert NOTEBOOK.exists()


def test_the_advanced_settings_are_in_a_collapsed_accordion_with_the_same_defaults():
    form = shown_forms().forms["model"]
    (advanced,) = [
        w
        for w in walk(form.widget)
        if isinstance(w, widgets.Accordion) and w.get_title(0) == form_module.ADVANCED
    ]
    assert advanced.selected_index is None  # collapsed
    inside = set(map(id, walk(advanced)))
    names = ("diameter", "flow_threshold", "cellprob_threshold", "batch_size")
    for name in names:
        assert id(form.fields[name]) in inside, name
    for name in set(form.fields) - set(names):
        assert id(form.fields[name]) not in inside, name
    assert [form.fields[n].value for n in names] == ["", 0.4, 0.0, 8]
