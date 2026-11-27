"""The example result and the run, after the ZeroCostDL4Mic Cellpose 2D notebook (section 6.1).

A dropdown of every image of the run and **Preview**: the chosen image is segmented with the
loaded model and the settings of cell 5 and drawn with Cellpose's own
``plot.show_segmentation`` (input, outlines, masks, flows) after one display panel per channel;
nothing is written. **Segment the whole experiment** runs every image. Both buttons are active
only once a model is loaded. A stand-in model (``cellpose_stub``): no GPU, no download.
"""

from pathlib import Path

import numpy as np
import pytest

from cellpose_stub import PLOT_TITLES, install
from morphobricks_preprocessing import notebook_cellpose
from morphobricks_preprocessing.notebook_cellpose_form import MODEL_CHANGED
from morphobricks_preprocessing.notebook_cellpose_preview import MaskResult, preview_figures
from morphobricks_preprocessing.segmentation_handoff import images_to_segment
from test_notebook_cellpose import (
    BATCH,
    CHANNELS,
    DRIVE,
    MODEL,
    OPEN,
    PREVIEW,
    open_and_choose,
    run_cell,
    source,
)

TESTS = Path(__file__).parent


@pytest.fixture
def cells(mixed_run, monkeypatch, tmp_path):
    state = install(monkeypatch, tmp_path)
    return type("C", (), {"ns": {}, "stub": state, "run": mixed_run})()


def shown_preview(cells):
    exec(compile(source(PREVIEW), "<cell 6>", "exec"), cells.ns)
    return cells.ns["workflow"].forms["preview"]


def test_the_dropdown_lists_every_image_of_the_run_on_the_first_without_a_mask(cells):
    open_and_choose(cells)
    run_cell(cells.ns, MODEL, device="cpu")
    fovs = cells.run.inputs.fovs()
    first = fovs[0].relative_path
    target = cells.run.masks / "Cells" / first  # a mask already there for the first image
    target.parent.mkdir(parents=True)
    target.write_bytes(b"not read here")
    dropdown = shown_preview(cells).fields["preview_image"]
    assert list(dropdown.options) == [f.relative_path for f in fovs]  # the run's order
    todo = images_to_segment(cells.run.inputs, "Cells", cells.run.masks)
    assert dropdown.value == todo[0].relative_path != first


def test_the_preview_draws_cellposes_panels_and_writes_nothing(cells):
    open_and_choose(cells)  # TUB as the cell channel, DNA as the nuclear channel
    run_cell(cells.ns, MODEL, device="cpu", diameter="20")
    run_cell(cells.ns, PREVIEW)
    workflow = cells.ns["workflow"]
    assert not cells.run.masks.exists()  # nothing written: no mask, no record
    (plotted,) = cells.stub.plotted
    (call,) = cells.stub.calls
    assert np.array_equal(plotted.img, call.x) and plotted.channels == [1, 2]
    assert np.array_equal(plotted.flow, np.full((*call.x.shape[1:], 3), 7, np.uint8))
    (result,) = workflow.previewed
    assert result.channels == (("TUB", "cell"), ("DNA", "nuclear"))
    channels, panels = preview_figures(workflow.previewed)
    assert [a.get_title() for a in channels.axes] == [
        "TUB (cell, display contrast)",
        "DNA (nuclear, display contrast)",
    ]
    assert tuple(a.get_title() for a in panels.axes) == PLOT_TITLES
    assert "Cell mask: 2 objects; diameter 20 pixels" in workflow.shown[-1]


def test_a_nuclei_preview_draws_one_channel_in_grey(cells):
    run_cell(cells.ns, OPEN, run_folder=str(cells.run.out), masks_folder=str(cells.run.masks))
    run_cell(cells.ns, CHANNELS, output="nuclei")
    cells.ns["workflow"].picker.nuclear.value = "DNA"
    run_cell(cells.ns, MODEL, device="cpu")
    run_cell(cells.ns, PREVIEW)
    assert cells.stub.plotted[0].channels == [0, 0]
    assert "Nuclei mask: 2 objects; diameter blank" in cells.ns["workflow"].shown[-1]


def test_another_image_and_other_settings_preview_again_without_reloading(cells):
    open_and_choose(cells)
    run_cell(cells.ns, MODEL, device="cpu")
    workflow = cells.ns["workflow"]
    fov = cells.run.inputs.fovs()[5]
    run_cell(cells.ns, PREVIEW, preview_image=fov.relative_path)
    assert np.array_equal(cells.stub.calls[-1].x, workflow.picker.plan.read(fov))
    model = workflow.forms["model"].fields
    model["diameter"].value, model["flow_threshold"].value = "25", 0.8
    run_cell(cells.ns, PREVIEW)
    assert cells.stub.calls[-1].kwargs["diameter"] == 25.0
    assert cells.stub.calls[-1].kwargs["flow_threshold"] == 0.8
    assert len(cells.stub.constructed) == 1  # the model was loaded once
    model["diameter"].value = "0"  # checked as Load the model checks it
    with pytest.raises(notebook_cellpose.CellStop, match="diameter"):
        run_cell(cells.ns, PREVIEW)


def test_a_changed_model_or_device_is_refused_until_loaded_again(cells):
    open_and_choose(cells)
    run_cell(cells.ns, MODEL, device="cpu")
    workflow = cells.ns["workflow"]
    model = workflow.forms["model"].fields
    for name, value in (("device", "cuda"), ("model", "cpsam"), ("custom_model_file", "/m")):
        before = model[name].value
        model[name].value = value
        for cell in (PREVIEW, BATCH):
            with pytest.raises(notebook_cellpose.CellStop) as stopped:
                run_cell(cells.ns, cell)
            assert str(stopped.value) == MODEL_CHANGED
        model[name].value = before
    assert cells.stub.calls == [] and len(cells.stub.constructed) == 1
    assert not cells.run.masks.exists()


def test_preview_and_run_buttons_follow_the_model_load(cells):
    open_and_choose(cells)
    exec(compile(source(PREVIEW), "<cell 6>", "exec"), cells.ns)
    exec(compile(source(BATCH), "<cell 7>", "exec"), cells.ns)
    workflow = cells.ns["workflow"]
    buttons = [workflow.forms[n].button for n in ("preview", "segment")]
    assert [b.disabled for b in buttons] == [True, True]
    assert buttons[1].description.strip() == "Segment the whole experiment"
    with pytest.raises(notebook_cellpose.CellStop, match="cuda was asked for"):
        run_cell(cells.ns, MODEL)  # a refused load leaves them off
    assert [b.disabled for b in buttons] == [True, True]
    run_cell(cells.ns, MODEL, device="cpu")
    assert [b.disabled for b in buttons] == [False, False]
    workflow.forms["channels"].fields["output"].value = "nuclei"  # the choice and model dropped
    assert [b.disabled for b in buttons] == [True, True]


def test_the_preview_takes_a_second_mask_without_change(monkeypatch, tmp_path):
    """The Both output (#440) adds a result: the figures follow."""
    stub = install(monkeypatch, tmp_path)
    stack = np.ones((2, 8, 8), np.float32)
    labels = np.zeros((8, 8), np.int32)
    flows = [np.zeros((8, 8, 3), np.uint8)]
    cell = MaskResult("Cell mask", stack, (("TUB", "cell"), ("DNA", "nuclear")), labels, flows)
    nuclei = MaskResult("Nuclei mask", stack[1:], (("DNA", "nuclear"),), labels, flows)
    figures = preview_figures([cell, nuclei])
    assert len(figures) == 4 and [p.channels for p in stub.plotted] == [[1, 2], [0, 0]]
    assert figures[3]._suptitle.get_text() == "Nuclei mask: 0 objects"


# a plain Jupyter kernel, with the stand-in model ------------------------------------------------


@pytest.mark.parametrize(("output", "masks"), [("cells", 1), ("both", 2)])
def test_the_preview_runs_in_a_plain_jupyter_kernel(mixed_run, tmp_path, output, masks):
    """Cells 2 to 6 in a real kernel (not Colab), the model a stand-in: the preview shows.

    With the output Both (#440) the image is segmented twice and both results are drawn.
    """
    nbformat = pytest.importorskip("nbformat")
    pytest.importorskip("ipykernel", reason="the dev extra installs ipykernel")
    from test_notebook_file import _execute

    new = nbformat.v4.new_code_cell
    stub = new(
        "import sys\n"
        f"sys.path.insert(0, {str(TESTS)!r})\n"
        "from _pytest.monkeypatch import MonkeyPatch\n"
        "from pathlib import Path\n"
        "from cellpose_stub import install\n"
        f"stub = install(MonkeyPatch(), Path({str(tmp_path)!r}))\n"
    )
    open_run = new(
        "form = workflow.forms['open']\n"
        f"form.fields['run_folder'].value = {str(mixed_run.out)!r}\n"
        f"form.fields['masks_folder'].value = {str(mixed_run.masks)!r}\n"
        "form.button.click()\n"
        "assert form.refusal == '', form.refusal\n"
    )
    choose = new(
        f"workflow.forms['channels'].fields['output'].value = {output!r}\n"
        "workflow.forms['channels'].button.click()\n"
        "workflow.picker.cell.value, workflow.picker.nuclear.value = 'TUB', 'DNA'\n"
    )
    load = new(
        "form = workflow.forms['model']\n"
        "form.fields['device'].value = 'cpu'\n"
        "form.button.click()\n"
        "assert form.refusal == '', form.refusal\n"
    )
    press = new(
        "form = workflow.forms['preview']\n"
        "print('BEFORE', form.button.disabled is False)\n"
        "print('IMAGES', len(form.fields['preview_image'].options))\n"
        "form.button.click()\n"
        "print('REFUSAL', repr(form.refusal))\n"
        "print('SHOWN', workflow.shown[-1])\n"
        f"print('WRITTEN', Path({str(mixed_run.masks)!r}).exists())\n"
        "print('PLOTTED', len(stub.plotted))\n"
    )
    code = {n: new(source(n)) for n in (DRIVE, OPEN, CHANNELS, MODEL, PREVIEW)}
    cells = [stub, code[DRIVE], code[OPEN], open_run, code[CHANNELS], choose, code[MODEL]]
    notebook = nbformat.v4.new_notebook(cells=[*cells, load, code[PREVIEW], press])
    _execute(notebook)

    errors = [o for c in notebook.cells for o in c.outputs if o.output_type == "error"]
    assert errors == []
    text = "".join(o.get("text", "") for o in notebook.cells[-1].outputs)
    assert "BEFORE True" in text and f"IMAGES {len(mixed_run.inputs.fovs())}" in text
    assert "REFUSAL ''" in text and "Preview of" in text and "nothing is written" in text
    assert "WRITTEN False" in text and f"PLOTTED {masks}" in text
    if output == "both":
        assert "Cell mask: 2 objects" in text and "Nuclei mask: 2 objects" in text


def test_the_run_line_names_the_model(cells, monkeypatch):
    # #574 item 4: the batch's progress line names the model that runs.
    labels = []
    real = notebook_cellpose.progress_html

    def spy(label, *args, **kwargs):
        labels.append(label)
        return real(label, *args, **kwargs)

    monkeypatch.setattr(notebook_cellpose, "progress_html", spy)
    open_and_choose(cells)
    run_cell(cells.ns, MODEL, device="cpu")
    cells.ns["workflow"].run()
    name = cells.ns["workflow"].loaded.name
    assert labels and set(labels) == {f"Running {name}"}
