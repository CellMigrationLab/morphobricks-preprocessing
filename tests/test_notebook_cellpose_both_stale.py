"""The stale-folder and reload guards of #446 with the output Both of #440.

#446's review made Preview and Segment refuse a run or masks folder other than the one opened,
drop the model on a failed reload, retire the forms of a replaced workflow and take the mask
type once typed. #440 added Both: two mask types and an optional separate nuclei model. These
cases check that each guard also covers the second mask type and the nuclei model. A stand-in
Cellpose (``cellpose_stub``) on a run whose images all have DNA and TUB.
"""

import html

import pytest

from cellpose_stub import install, two_channel_run
from morphobricks_preprocessing import segmentation_cellpose as cp
from morphobricks_preprocessing.notebook_cellpose_guard import REPLACED, RUN_CHANGED
from morphobricks_preprocessing.notebook_stop import CellStop
from morphobricks_preprocessing.segmentation_record import read_record, record_path
from test_notebook_cellpose import BATCH, CHANNELS, MODEL, OPEN, PREVIEW, run_cell, source

NUCLEI_MODEL = {"separate_nuclei": True, "nuclei_model": "cpsam", "nuclei_diameter": "12"}
NOT_LOADED = "The nuclei model did not load: press Load the model again."


@pytest.fixture
def both(monkeypatch, tmp_path):
    """Cells 3 to 5 run for Both with a separate nuclei model; 6 and 7 shown, not pressed."""
    stub = install(monkeypatch, tmp_path)
    cells = type("C", (), {"ns": {}, "stub": stub, "run": two_channel_run(tmp_path / "run")})()
    run_cell(cells.ns, OPEN, run_folder=str(cells.run.out), masks_folder=str(cells.run.masks))
    run_cell(cells.ns, CHANNELS, output="both")
    picker = cells.ns["workflow"].picker
    picker.cell.value, picker.nuclear.value = "TUB", "DNA"
    run_cell(cells.ns, MODEL, device="cpu", **NUCLEI_MODEL)
    for cell in (PREVIEW, BATCH):
        exec(compile(source(cell), f"<cell {cell + 1}>", "exec"), cells.ns)
    workflow = cells.ns["workflow"]
    assert workflow.loaded is not None and workflow.nuclei_loaded is not None
    return cells


def press(form):
    """Press the form's button as a click would, even when disabled; return the refusal."""
    form.button.click()
    return form.refusal


def nothing_written(folder):
    return all(
        not (folder / name).exists() and not record_path(folder, name).exists()
        for name in ("Cells", "Nuclei")
    )


def disabled(workflow):
    return all(workflow.forms[step].button.disabled for step in ("preview", "segment"))


@pytest.mark.parametrize("box", ["masks_folder", "run_folder"])
def test_a_folder_edited_without_open_refuses_both_mask_types(both, box):
    workflow, masks = both.ns["workflow"], both.run.masks
    elsewhere = masks.parent / "other_masks"
    field = workflow.forms["open"].fields[box]
    field.value = str(elsewhere if box == "masks_folder" else both.run.experiment)
    for step in ("preview", "segment"):
        assert press(workflow.forms[step]) == RUN_CHANGED
        assert html.escape(RUN_CHANGED) in workflow.shown[-1]
    assert both.stub.calls == [] and workflow.results == {}
    assert nothing_written(masks) and not elsewhere.exists()
    field.value = str(masks if box == "masks_folder" else both.run.out)
    assert press(workflow.forms["segment"]) == ""  # the folders opened: both batches run
    fovs = len(both.run.inputs.fovs())
    assert [len(read_record(masks, t).batches[0].images) for t in ("Cells", "Nuclei")] == [
        fovs,
        fovs,
    ]


def test_a_refused_open_drops_the_nuclei_model_and_both_mask_types(both, tmp_path):
    workflow = both.ns["workflow"]
    opener = workflow.forms["open"]
    opener.fields["run_folder"].value = str(tmp_path / "no_run")
    opener.fields["masks_folder"].value = str(tmp_path / "new_masks")
    assert press(opener)
    assert workflow.loaded is None and workflow.nuclei_loaded is None
    assert workflow.nuclei_settings is None and workflow.mask_types == {}
    assert disabled(workflow)
    assert press(workflow.forms["segment"]) == "Run cell 5 first."
    assert both.stub.calls == [] and nothing_written(both.run.masks)


def test_a_failed_nuclei_reload_drops_both_models_and_disables_preview_and_segment(
    both, monkeypatch
):
    workflow = both.ns["workflow"]
    real = cp.load_model

    def refuse_nuclei(name, *args, **kwargs):
        if name == "cpsam":
            raise cp.SegmentationError("no weights")
        return real(name, *args, **kwargs)

    monkeypatch.setattr(cp, "load_model", refuse_nuclei)
    with pytest.raises(CellStop, match="no weights"):
        run_cell(both.ns, MODEL, device="cpu", **NUCLEI_MODEL)
    assert workflow.loaded is None and workflow.nuclei_loaded is None
    assert disabled(workflow)
    for step in ("preview", "segment"):  # a press anyway says why, not "run cell 5"
        assert press(workflow.forms[step]) == NOT_LOADED
    assert both.stub.calls == [] and nothing_written(both.run.masks)
    monkeypatch.setattr(cp, "load_model", real)
    run_cell(both.ns, MODEL, device="cpu", **NUCLEI_MODEL)  # loads again: both work
    assert not disabled(workflow)
    assert press(workflow.forms["segment"]) == ""
    assert read_record(both.run.masks, "Nuclei").batches[0].model_name == "cpsam"


def test_the_nuclei_mask_type_is_taken_once_typed_and_a_change_drops_both_models(both):
    workflow = both.ns["workflow"]
    channels = workflow.forms["channels"]
    nuclei_type = channels.fields["nuclei_mask_type"]
    assert nuclei_type.continuous_update is False
    assert channels.fields["mask_type"].continuous_update is False
    assert workflow.loaded is not None and workflow.nuclei_loaded is not None
    nuclei_type.value = "Nuclei2"
    assert workflow.picker is None and workflow.mask_types == {}
    assert workflow.loaded is None and workflow.nuclei_loaded is None
    assert disabled(workflow)
    assert press(workflow.forms["segment"]) == "Run cell 5 first."
    assert both.stub.calls == [] and nothing_written(both.run.masks)


def test_running_cell_3_again_retires_the_nuclei_fields_too(both):
    old = both.ns["workflow"]
    exec(compile(source(OPEN), "<cell 3>", "exec"), both.ns)
    assert old.replaced and both.ns["workflow"] is not old
    model, channels = old.forms["model"], old.forms["channels"]
    assert all(model.fields[name].disabled for name in NUCLEI_MODEL)
    assert channels.fields["nuclei_mask_type"].disabled
    assert all(field.disabled for field in (*old.picker.number_fields, old.picker.by))
    assert press(old.forms["segment"]) == REPLACED
    assert both.stub.calls == [] and nothing_written(both.run.masks)


def test_a_nuclei_load_failing_with_any_error_drops_both_models(both, monkeypatch):
    """Not only a refusal: an error such as GPU memory exhausted drops both models too."""
    workflow = both.ns["workflow"]
    real = cp.load_model

    def out_of_memory(name, *args, **kwargs):
        if name == "cpsam":
            raise RuntimeError("CUDA out of memory")
        return real(name, *args, **kwargs)

    monkeypatch.setattr(cp, "load_model", out_of_memory)
    run_cell(both.ns, MODEL, device="cpu", **NUCLEI_MODEL)  # the form shows the error
    assert workflow.loaded is None and workflow.nuclei_loaded is None
    assert disabled(workflow)
    for step in ("preview", "segment"):
        assert press(workflow.forms[step]) == NOT_LOADED
    assert both.stub.calls == [] and nothing_written(both.run.masks)
