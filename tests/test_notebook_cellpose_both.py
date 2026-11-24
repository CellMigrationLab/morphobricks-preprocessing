"""The output Both of the Cellpose notebook (#440): two mask types from one choice of channels.

Each image is segmented twice: the cells from the cell channel and the nuclear channel stacked
as (cell, nuclear), the nuclei from the nuclear channel alone. They are two mask types
(``Cells`` and ``Nuclei`` by default, both editable), each with its own record entry, its own
skip, overwrite and rollback, and optionally its own model and settings. Nothing pairs a cell
with a nucleus. A stand-in Cellpose (``cellpose_stub``) on a run whose images all have DNA and
TUB.
"""

import json

import numpy as np
import pytest
import tifffile

from cellpose_stub import install, labels_for, two_channel_run
from morphobricks_preprocessing import notebook_cellpose
from morphobricks_preprocessing import segmentation_cellpose as cp
from morphobricks_preprocessing.notebook_cellpose_form import MODEL_CHANGED
from morphobricks_preprocessing.segmentation_record import read_record, record_path
from test_notebook_cellpose import BATCH, CHANNELS, MODEL, NEXT, OPEN, PREVIEW, run_cell


@pytest.fixture
def cells(monkeypatch, tmp_path):
    state = install(monkeypatch, tmp_path)
    run = two_channel_run(tmp_path / "run")
    return type("C", (), {"ns": {}, "stub": state, "run": run})()


def choose_both(cells, cell="TUB", nuclear="DNA", **fields):
    """Open the run, choose the output Both in cell 4, then the two channels."""
    run = cells.run
    run_cell(cells.ns, OPEN, run_folder=str(run.out), masks_folder=str(run.masks))
    run_cell(cells.ns, CHANNELS, output="both", **fields)
    picker = cells.ns["workflow"].picker
    picker.cell.value, picker.nuclear.value = cell, nuclear
    return picker


def counts(result):
    return {k: v for k, v in result.counts().items() if v}


def test_both_writes_two_mask_types_each_with_its_own_record(cells):
    picker = choose_both(cells)
    assert [(t.output, t.channel_ids, t.roles) for t in picker.targets] == [
        ("cells", ("TUB", "DNA"), ("cell", "nuclear")),
        ("nuclei", ("DNA",), ("nuclear",)),
    ]
    assert "Cell mask from TUB (cell), DNA (nuclear); Nuclei mask from DNA (nuclear)" in (
        picker.message.value
    )
    run_cell(cells.ns, MODEL, device="cpu")
    workflow, fovs = cells.ns["workflow"], cells.run.inputs.fovs()
    written = []
    workflow.on_masks_written = written.append
    run_cell(cells.ns, BATCH)
    assert len(written) == 2  # each batch handed on
    assert set(workflow.results) == {"Cells", "Nuclei"}
    for result in workflow.results.values():
        assert counts(result) == {"written": len(fovs)}
    # each image segmented twice: (cell, nuclear) for the cells, the nuclear channel alone
    stacks = [call.x for call in cells.stub.calls]
    assert len(stacks) == 2 * len(fovs)
    cell_plan, nuclei_plan = (t.plan for t in picker.targets)
    assert np.array_equal(stacks[0], cell_plan.read(fovs[0]))
    assert np.array_equal(stacks[len(fovs)], nuclei_plan.read(fovs[0]))
    assert [s.shape[0] for s in stacks] == [2] * len(fovs) + [1] * len(fovs)
    cells_batch = read_record(cells.run.masks, "Cells").batches[0]
    nuclei_batch = read_record(cells.run.masks, "Nuclei").batches[0]
    assert cells_batch.channel_ids == ("TUB", "DNA") and nuclei_batch.channel_ids == ("DNA",)
    assert cells_batch.parameters["output"] == "cells"
    assert nuclei_batch.parameters["output"] == "nuclei"
    assert cells_batch.parameters["channel_roles"] == [
        {"channel_id": "TUB", "role": "cell"},
        {"channel_id": "DNA", "role": "nuclear"},
    ]
    assert nuclei_batch.parameters["channel_roles"] == [{"channel_id": "DNA", "role": "nuclear"}]
    assert cells_batch.batch_id != nuclei_batch.batch_id
    assert [i.image for i in nuclei_batch.images] == [f.relative_path for f in fovs]
    # no pairing: each mask is Cellpose's labels as returned, neither renumbered to the other
    for mask_type in ("Cells", "Nuclei"):
        stored = tifffile.imread(cells.run.masks / mask_type / fovs[0].relative_path)
        assert np.array_equal(stored, labels_for((8, 8)))
        text = record_path(cells.run.masks, mask_type).read_text(encoding="utf-8")
        assert "pair" not in text.lower()


def test_both_mask_types_are_editable_and_must_differ(cells):
    choose_both(cells, mask_type="MyCells", nuclei_mask_type="MyNuclei")
    assert cells.ns["workflow"].mask_types == {"cells": "MyCells", "nuclei": "MyNuclei"}
    run_cell(cells.ns, MODEL, device="cpu")
    run_cell(cells.ns, BATCH)
    assert read_record(cells.run.masks, "MyCells").batches[0].parameters["output"] == "cells"
    assert read_record(cells.run.masks, "MyNuclei").batches[0].parameters["output"] == "nuclei"
    with pytest.raises(notebook_cellpose.CellStop, match="must differ"):
        run_cell(cells.ns, CHANNELS, output="both", mask_type="Cells", nuclei_mask_type="cells")


def test_one_model_by_default_and_a_nuclei_model_of_their_own_in_advanced(cells):
    choose_both(cells)
    run_cell(cells.ns, MODEL, device="cpu", diameter="30")
    run_cell(cells.ns, BATCH)
    batches = [read_record(cells.run.masks, t).batches[-1] for t in ("Cells", "Nuclei")]
    assert [b.model_name for b in batches] == ["cpsam_v2", "cpsam_v2"]
    assert [b.parameters["diameter"] for b in batches] == [30.0, 30.0]
    assert len(cells.stub.constructed) == 1  # one model for both
    run_cell(
        cells.ns,
        MODEL,
        device="cpu",
        diameter="30",
        separate_nuclei=True,
        nuclei_model="cpsam",
        nuclei_diameter="12",
        nuclei_flow_threshold=0.7,
    )
    assert len(cells.stub.constructed) == 3  # the cells' model, then the nuclei's
    run_cell(cells.ns, BATCH, overwrite=True)
    cells_batch, nuclei_batch = (
        read_record(cells.run.masks, t).batches[-1] for t in ("Cells", "Nuclei")
    )
    assert (cells_batch.model_name, cells_batch.parameters["diameter"]) == ("cpsam_v2", 30.0)
    assert (nuclei_batch.model_name, nuclei_batch.parameters["diameter"]) == ("cpsam", 12.0)
    assert nuclei_batch.parameters["flow_threshold"] == 0.7
    assert cells_batch.parameters["flow_threshold"] == 0.4


def test_ticking_a_nuclei_model_after_loading_needs_loading_again(cells):
    choose_both(cells)
    run_cell(cells.ns, MODEL, device="cpu")
    workflow = cells.ns["workflow"]
    workflow.forms["model"].fields["separate_nuclei"].value = True
    with pytest.raises(notebook_cellpose.CellStop) as stopped:
        run_cell(cells.ns, PREVIEW)
    assert str(stopped.value) == MODEL_CHANGED and cells.stub.calls == []


def test_a_separate_nuclei_model_is_refused_for_one_output(cells):
    run = cells.run
    run_cell(cells.ns, OPEN, run_folder=str(run.out), masks_folder=str(run.masks))
    run_cell(cells.ns, CHANNELS, output="nuclei")
    cells.ns["workflow"].picker.nuclear.value = "DNA"
    with pytest.raises(notebook_cellpose.CellStop, match="for the output Both only"):
        run_cell(cells.ns, MODEL, device="cpu", separate_nuclei=True)
    assert cells.stub.constructed == []  # refused before anything loads


def test_each_mask_type_is_skipped_and_overwritten_on_its_own(cells):
    choose_both(cells)
    run_cell(cells.ns, MODEL, device="cpu")
    run_cell(cells.ns, BATCH)
    workflow, fovs = cells.ns["workflow"], cells.run.inputs.fovs()
    (cells.run.masks / "Cells" / fovs[0].relative_path).unlink()  # one cell mask gone
    nuclei = (cells.run.masks / "Nuclei" / fovs[1].relative_path).read_bytes()
    run_cell(cells.ns, BATCH)
    assert counts(workflow.results["Cells"]) == {"written": 1, "skipped": len(fovs) - 1}
    assert counts(workflow.results["Nuclei"]) == {"skipped": len(fovs)}
    assert (cells.run.masks / "Nuclei" / fovs[1].relative_path).read_bytes() == nuclei
    run_cell(cells.ns, BATCH, overwrite=True)
    assert counts(workflow.results["Cells"]) == {"written": len(fovs)}
    assert counts(workflow.results["Nuclei"]) == {"written": len(fovs)}
    assert [len(read_record(cells.run.masks, t).batches) for t in ("Cells", "Nuclei")] == [3, 3]


def test_failed_nuclei_images_leave_the_cells_written(cells):
    choose_both(cells)
    run_cell(cells.ns, MODEL, device="cpu")

    def fail_nuclei(stack):
        if stack.shape[0] == 1:
            raise RuntimeError("out of memory")

    cells.stub.fail = fail_nuclei
    run_cell(cells.ns, BATCH)
    workflow, fovs = cells.ns["workflow"], cells.run.inputs.fovs()
    assert counts(workflow.results["Cells"]) == {"written": len(fovs)}
    assert counts(workflow.results["Nuclei"]) == {"failed": len(fovs)}
    assert all((cells.run.masks / "Cells" / f.relative_path).is_file() for f in fovs)
    assert not list((cells.run.masks / "Nuclei").rglob("*.tif"))


def test_a_rolled_back_cells_batch_does_not_stop_the_nuclei(cells, monkeypatch):
    choose_both(cells)
    run_cell(cells.ns, MODEL, device="cpu")
    real = cp.write_record

    def refuse_cells(root, mask_type, batch):
        if mask_type == "Cells":
            raise OSError("disk full")
        return real(root, mask_type, batch)

    monkeypatch.setattr(cp, "write_record", refuse_cells)
    run_cell(cells.ns, BATCH)  # a stopped batch is reported, not raised
    workflow, fovs = cells.ns["workflow"], cells.run.inputs.fovs()
    stopped = workflow.results["Cells"]
    assert stopped.stopped and cp.ROLLED_BACK_REMOVED in stopped.rows()[0][2]
    assert not list((cells.run.masks / "Cells").rglob("*.tif"))  # the rollback removed it
    assert counts(workflow.results["Nuclei"]) == {"written": len(fovs)}
    assert len(read_record(cells.run.masks, "Nuclei").batches[0].images) == len(fovs)


def test_a_refused_cells_record_still_lets_the_nuclei_run_then_stops_the_cell(cells):
    choose_both(cells)
    run_cell(cells.ns, MODEL, device="cpu")
    broken = record_path(cells.run.masks, "Cells")
    broken.parent.mkdir(parents=True)
    broken.write_text("{not json", encoding="utf-8")
    with pytest.raises(notebook_cellpose.CellStop) as stopped:
        run_cell(cells.ns, BATCH)
    workflow, fovs = cells.ns["workflow"], cells.run.inputs.fovs()
    assert "Cells" not in workflow.results
    assert counts(workflow.results["Nuclei"]) == {"written": len(fovs)}
    assert any("Mask type Cells stopped" in card for card in workflow.shown)
    assert str(stopped.value) in " ".join(workflow.shown)
    assert broken.read_text(encoding="utf-8") == "{not json"  # never repaired


def test_the_preview_shows_both_masks_and_writes_nothing(cells):
    choose_both(cells)
    run_cell(cells.ns, MODEL, device="cpu")
    run_cell(cells.ns, PREVIEW)
    workflow = cells.ns["workflow"]
    assert not cells.run.masks.exists()  # nothing written: no mask, no record
    assert [r.name for r in workflow.previewed] == ["Cell mask", "Nuclei mask"]
    assert [r.channels for r in workflow.previewed] == [
        (("TUB", "cell"), ("DNA", "nuclear")),
        (("DNA", "nuclear"),),
    ]
    assert [p.channels for p in cells.stub.plotted] == [[1, 2], [0, 0]]
    assert len(cells.stub.calls) == 2
    shown = workflow.shown[-1]
    assert "Cell mask: 2 objects" in shown and "Nuclei mask: 2 objects" in shown


def test_the_next_steps_list_both_mask_types_and_pair_nothing(cells):
    choose_both(cells, mask_type="MyCells")
    run_cell(cells.ns, NEXT)
    text = cells.ns["workflow"].shown[-1]
    assert "[masks.MyCells]" in text and "[masks.Nuclei]" in text
    assert "the 2 new mask types" in text and "never pairs a cell with a nucleus" in text
    assert "MorphoRelate" in text


@pytest.mark.parametrize(
    ("cell", "nuclear", "message"),
    [
        ("TUB", "", "BOTH_NEEDS"),  # no nuclear channel
        ("", "DNA", "BOTH_NEEDS"),  # no cell channel
        ("", "", "NO_CHANNEL"),  # neither
    ],
)
def test_both_needs_the_cell_and_the_nuclear_channel(cells, cell, nuclear, message):
    message = getattr(cp, message)
    picker = choose_both(cells, cell=cell, nuclear=nuclear)
    assert picker.targets == () and picker.plan is None and picker.refusal == message
    with pytest.raises(notebook_cellpose.CellStop) as stopped:
        run_cell(cells.ns, MODEL, device="cpu")
    assert str(stopped.value) == message and cells.stub.constructed == []


def test_the_record_of_both_is_plain_json_without_a_partner(cells):
    choose_both(cells)
    run_cell(cells.ns, MODEL, device="cpu")
    run_cell(cells.ns, BATCH)
    for mask_type, other in (("Cells", "Nuclei"), ("Nuclei", "Cells")):
        data = json.loads(record_path(cells.run.masks, mask_type).read_text(encoding="utf-8"))
        assert other not in json.dumps(data["batches"][0]["parameters"])


# review fixes: no silent substitution, one failure does not stop the other batch ------------------


def settings_and_cell_model(workflow):
    """Set the cells' settings and load their model, as cell 5 does first."""
    workflow.set_settings("", 0.4, 0.0, 8)
    workflow.load("cpsam_v2", "", "cpu", False, False)


def nothing_written(cells, *names):
    masks = cells.run.masks
    return all(not (masks / n).exists() and not record_path(masks, n).exists() for n in names)


@pytest.mark.parametrize("failed_load", [False, True])
def test_nuclei_settings_without_a_loaded_nuclei_model_are_refused(cells, monkeypatch, failed_load):
    choose_both(cells)
    workflow = cells.ns["workflow"]
    settings_and_cell_model(workflow)
    workflow.set_nuclei_settings("12", 0.7, 0.0, 8)
    if failed_load:  # the nuclei model does not load

        def refuse(*args, **kwargs):
            raise cp.SegmentationError("no weights")

        monkeypatch.setattr(cp, "load_model", refuse)
        with pytest.raises(notebook_cellpose.CellStop, match="no weights"):
            workflow.load_nuclei("cpsam", "", "cpu", False, False)
    assert workflow.nuclei_loaded is None
    with pytest.raises(
        notebook_cellpose.CellStop, match="The nuclei have their own model: load it first"
    ):
        workflow.run()
    assert cells.stub.calls == [] and workflow.results == {}  # refused before any image
    with pytest.raises(
        notebook_cellpose.CellStop, match="The nuclei have their own model: load it first"
    ):
        workflow.preview()  # the preview refuses the nuclei the same way
    assert nothing_written(cells, "Cells", "Nuclei")


def test_a_failing_hook_after_cells_still_runs_the_nuclei_then_raises(cells):
    choose_both(cells)
    run_cell(cells.ns, MODEL, device="cpu")
    workflow, fovs = cells.ns["workflow"], cells.run.inputs.fovs()
    handed = []

    def hook(result):
        handed.append(result)
        if len(handed) == 1:
            raise RuntimeError("hook failed")

    workflow.on_masks_written = hook
    with pytest.raises(RuntimeError, match="hook failed"):
        workflow.run()
    assert len(handed) == 2  # the nuclei were handed on too
    assert counts(workflow.results["Cells"]) == {"written": len(fovs)}
    assert counts(workflow.results["Nuclei"]) == {"written": len(fovs)}
    assert len(read_record(cells.run.masks, "Nuclei").batches[0].images) == len(fovs)
    assert any("Mask type Cells stopped" in card for card in workflow.shown)


def test_a_failed_nuclei_load_says_so_not_that_the_model_changed(cells, monkeypatch):
    choose_both(cells)
    real = cp.load_model

    def refuse_nuclei(name, *args, **kwargs):
        if name == "cpsam":
            raise cp.SegmentationError("no weights")
        return real(name, *args, **kwargs)

    monkeypatch.setattr(cp, "load_model", refuse_nuclei)
    with pytest.raises(notebook_cellpose.CellStop, match="no weights"):
        run_cell(cells.ns, MODEL, device="cpu", separate_nuclei=True, nuclei_model="cpsam")
    for cell in (PREVIEW, BATCH):
        with pytest.raises(notebook_cellpose.CellStop) as stopped:
            run_cell(cells.ns, cell)
        assert "nuclei model did not load" in str(stopped.value)
        assert str(stopped.value) != MODEL_CHANGED
    assert cells.stub.calls == [] and nothing_written(cells, "Cells", "Nuclei")


def test_changing_the_output_or_opening_a_run_forgets_the_mask_types(cells):
    choose_both(cells)
    workflow = cells.ns["workflow"]
    assert set(workflow.mask_types) == {"cells", "nuclei"}
    workflow.drop_choice()
    assert workflow.mask_types == {}
    choose_both(cells)
    run = cells.run
    run_cell(cells.ns, OPEN, run_folder=str(run.out), masks_folder=str(run.masks))
    assert workflow.mask_types == {} and workflow.picker is None
