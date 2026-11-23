"""The channel step of the Cellpose notebook: a cell and a nuclear channel, and the output.

Owner, 23 November 2026: the cell channel and the nuclear channel are both optional, at least
one is needed, both by channel ID. A **cell mask** reads the cell channel, then the nuclear
channel if one is chosen; a **nuclei mask** reads the nuclear channel only. The mask type
follows the output (Cells / Nuclei) and stays editable. The record names each channel's role
and the output. The channels are still resolved by ID by ``resolve_segmentation``.
"""

import numpy as np
import pytest

from cellpose_stub import install
from morphobricks_preprocessing import notebook_cellpose
from morphobricks_preprocessing import segmentation_cellpose as cp
from morphobricks_preprocessing.segmentation_record import SegmentationError, read_record
from test_notebook_cellpose import BATCH, CHANNELS, MODEL, OPEN, open_and_choose, run_cell
from test_segmentation_cellpose import make_job


@pytest.fixture
def cells(mixed_run, monkeypatch, tmp_path):
    state = install(monkeypatch, tmp_path)
    return type("C", (), {"ns": {}, "stub": state, "run": mixed_run})()


def choose(cells, output, cell, nuclear, mask_type=None):
    """Open the run, choose ``output`` (and a mask type) in cell 4, then the two channels."""
    run = cells.run
    run_cell(cells.ns, OPEN, run_folder=str(run.out), masks_folder=str(run.masks))
    fields = {"output": output} | ({"mask_type": mask_type} if mask_type else {})
    run_cell(cells.ns, CHANNELS, **fields)
    picker = cells.ns["workflow"].picker
    picker.cell.value, picker.nuclear.value = cell, nuclear
    return picker


def test_both_channels_start_blank_and_neither_is_refused(cells):
    picker = open_and_choose(cells, ids=("", ""))
    assert [d.description for d in picker.dropdowns] == ["Cell channel", "Nuclear channel"]
    assert picker.plan is None and picker.refusal == cp.NO_CHANNEL
    with pytest.raises(notebook_cellpose.CellStop, match="neither is chosen"):
        run_cell(cells.ns, MODEL, device="cpu")


def test_a_cell_mask_reads_the_cell_channel_then_the_nuclear_one_and_records_both(cells):
    picker = choose(cells, "cells", "ACTIN", "DNA")
    assert picker.channel_ids == ("ACTIN", "DNA")
    assert picker.channel_roles == ("cell", "nuclear")
    assert "Cell mask from ACTIN (cell), DNA (nuclear)" in picker.message.value
    run_cell(cells.ns, MODEL, device="cpu")
    run_cell(cells.ns, BATCH)
    fov = cells.run.inputs.fovs()[0]
    assert np.array_equal(cells.stub.calls[0].x, picker.plan.read(fov))
    batch = read_record(cells.run.masks, "Cells").batches[0]
    assert batch.channel_ids == ("ACTIN", "DNA") and batch.parameters["output"] == "cells"
    assert batch.parameters["channel_roles"] == [
        {"channel_id": "ACTIN", "role": "cell"},
        {"channel_id": "DNA", "role": "nuclear"},
    ]


def test_a_cell_mask_needs_no_nuclear_channel_but_needs_a_cell_channel(cells):
    picker = choose(cells, "cells", "TUB", "")
    assert picker.channel_ids == ("TUB",) and picker.channel_roles == ("cell",)
    picker.cell.value, picker.nuclear.value = "", "DNA"
    assert picker.plan is None and picker.refusal.startswith("A cell mask needs a cell channel")


def test_a_nuclei_mask_reads_the_nuclear_channel_only_and_is_named_nuclei(cells):
    picker = choose(cells, "nuclei", "TUB", "DNA")
    workflow = cells.ns["workflow"]
    assert workflow.mask_type == "Nuclei"  # the mask type follows the output
    assert picker.channel_ids == ("DNA",) and picker.channel_roles == ("nuclear",)
    run_cell(cells.ns, MODEL, device="cpu")
    run_cell(cells.ns, BATCH)
    assert all(call.x.shape[0] == 1 for call in cells.stub.calls)
    batch = read_record(cells.run.masks, "Nuclei").batches[0]
    assert batch.channel_ids == ("DNA",) and batch.parameters["output"] == "nuclei"
    assert batch.parameters["channel_roles"] == [{"channel_id": "DNA", "role": "nuclear"}]
    picker.nuclear.value = ""
    assert picker.refusal.startswith("A nuclei mask needs a nuclear channel")


def test_the_mask_type_follows_the_output_and_stays_editable(cells):
    picker = choose(cells, "nuclei", "", "DNA", mask_type="MyNuclei")
    workflow = cells.ns["workflow"]
    assert workflow.mask_type == "MyNuclei" and picker.output == "nuclei"
    fields = workflow.forms["channels"].fields
    fields["output"].value = "cells"
    assert fields["mask_type"].value == "MyNuclei"  # typed by the user: kept
    fields["mask_type"].value = "Nuclei"
    fields["output"].value = "nuclei"
    fields["output"].value = "cells"
    assert fields["mask_type"].value == "Cells"


def test_changing_the_output_after_choosing_drops_the_choice(cells):
    choose(cells, "cells", "TUB", "DNA")
    run_cell(cells.ns, MODEL, device="cpu")
    workflow = cells.ns["workflow"]
    workflow.forms["channels"].fields["output"].value = "nuclei"
    assert workflow.picker is None and workflow.loaded is None
    with pytest.raises(notebook_cellpose.CellStop, match="Run cell 5 first"):
        run_cell(cells.ns, BATCH)  # the model is dropped with the choice


def test_an_unknown_output_is_refused(cells):
    run = cells.run
    run_cell(cells.ns, OPEN, run_folder=str(run.out), masks_folder=str(run.masks))
    with pytest.raises(notebook_cellpose.CellStop, match="output must be one of"):
        cells.ns["workflow"].choose_channels("Cells", "both")


@pytest.mark.parametrize(
    ("ids", "roles", "output"),
    [
        (("DNA", "TUB"), ("nuclear", "cell"), "cells"),  # nuclear first
        (("DNA",), ("nuclear",), "cells"),  # a cell mask without a cell channel
        (("DNA", "TUB"), ("cell", "nuclear"), "nuclei"),  # a nuclei mask reads one channel
        (("DNA",), ("cell",), None),  # roles without an output
        ((), (), "cells"),  # an output without roles
        (("DNA",), ("membrane",), "cells"),
    ],
)
def test_roles_that_do_not_fit_the_output_are_refused(ids, roles, output):
    with pytest.raises(SegmentationError, match="do not fit the output"):
        cp.channel_roles_entry(ids, roles, output)


def test_a_job_without_roles_records_none_never_guessed(mixed_run, monkeypatch, tmp_path):
    install(monkeypatch, tmp_path)
    result = cp.run_batch(make_job(mixed_run), batch_id="b1")
    assert result.batch.parameters["channel_roles"] is None
    assert result.batch.parameters["output"] is None
    with pytest.raises(SegmentationError, match="do not fit"):
        make_job(mixed_run, channel_roles=("nuclear", "cell"), output="cells")
